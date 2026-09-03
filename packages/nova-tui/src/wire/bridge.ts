/**
 * 反向通道路由（wire 层）。
 *
 * 反向通道 = 后端向前端发起的帧。当前词汇（对齐 Python ``UIContext``）：
 * - ``ui/request``：反向原语询问（project trust / OAuth / 扩展询问）——
 *   线上 params 为 ``{id, component: {componentType, ...payload}}``，
 *   需要前端应答（``ui/response``）；
 * - ``ui/notify``：单向通知（如 package_progress 进度）——
 *   线上 params 为 ``{method, ...payload}``，无需应答；
 * - M4 将加入 ``tool/invoke`` 家族（反向工具通道），同样在这里路由。
 *
 * 本模块只做帧 → 词汇的翻译与应答回写，不解释对话框语义（归前端）。
 */

import type { ReverseFrame, WireClient } from './client.js';

/** bridge 需要的最小传输面（结构类型——便于测试替身与将来其他宿主复用）。 */
export interface ReverseChannel {
  onReverse(sink: (frame: ReverseFrame) => void): void;
  send(frame: Record<string, unknown>): void;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

/** 反向原语请求（需要前端应答）。 */
export interface UIRequest {
  /** 请求 id（应答时原样回传）。 */
  id: string;
  /** 原语组件类型（``select``/``confirm``/``input``/…）。 */
  component: string;
  /** 组件载荷（标题/选项/默认值等，语义归前端）。 */
  params: Record<string, unknown>;
}

/** 单向通知（无需应答，载荷由通知名自定义）。 */
export interface UINotice {
  /** 通知名（如 ``package_progress``）。 */
  name: string;
  params: Record<string, unknown>;
}

export class ReverseBridge {
  private requestHandler: ((request: UIRequest) => void) | undefined;
  private noticeHandler: ((notice: UINotice) => void) | undefined;
  private cancelHandler: ((id: string) => void) | undefined;

  constructor(private readonly channel: ReverseChannel) {
    channel.onReverse((frame) => this.route(frame));
  }

  /** 注册反向原语请求处理器（前端实现对话框后调 ``respond`` 应答）。 */
  onRequest(handler: (request: UIRequest) => void): void {
    this.requestHandler = handler;
  }

  /** 注册单向通知处理器（进度提醒等）。 */
  onNotice(handler: (notice: UINotice) => void): void {
    this.noticeHandler = handler;
  }

  /** 注册撤销处理器（后端 abort 竞速胜出 → 关闭对应对话框）。 */
  onCancel(handler: (id: string) => void): void {
    this.cancelHandler = handler;
  }

  /** 应答反向原语请求。 */
  respond(id: string, result: unknown): void {
    this.channel.send({
      jsonrpc: '2.0',
      id: `ui-resp-${id}`,
      method: 'ui/response',
      params: { id, result },
    });
  }

  /** 上报本端支持的反向原语能力子集（system/capabilities）。 */
  sendCapabilities(capabilities: string[]): void {
    this.channel.send({
      jsonrpc: '2.0',
      id: `caps-${String(Date.now())}`,
      method: 'system/capabilities',
      params: { capabilities },
    });
  }

  private route(frame: ReverseFrame): void {
    if (frame.method === 'ui/request') {
      if (frame.id === undefined) return;
      const component = asRecord(frame.params?.component);
      const { componentType, ...payload } = component;
      if (this.requestHandler === undefined) {
        // NoOp 等价物：无 handler 时立即应答 cancelled——
        // 不让后端的 300s 超时成为常规路径（语义与 capability 未宣告一致：
        // "没人应答"与"不支持"对后端同为取消）
        this.respond(frame.id, { cancelled: true });
        return;
      }
      this.requestHandler({
        id: frame.id,
        component: typeof componentType === 'string' ? componentType : 'unknown',
        params: payload,
      });
      return;
    }
    if (frame.method === 'ui/notify') {
      const { method, ...payload } = frame.params ?? {};
      this.noticeHandler?.({
        name: typeof method === 'string' ? method : 'unknown',
        params: payload,
      });
      return;
    }
    if (frame.method === 'ui/cancel') {
      // 撤销帧：后端 abort 竞速胜出 → 关闭对应 id 的对话框
      const id = frame.params?.id;
      if (typeof id === 'string') this.cancelHandler?.(id);
      return;
    }
    // 其余反向方法（tool/invoke，M4）：暂静默——向前兼容
  }
}
