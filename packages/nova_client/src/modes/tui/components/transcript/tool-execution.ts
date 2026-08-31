/**
 * 工具调用卡片（复刻 pi components/tool-execution.ts 的视觉模型）。
 *
 * header：状态背景色横跨（pending/success/error）+ 工具名 + 主参数摘要；
 * 内容：slots 渲染器产块 → 薄块适配器；无渲染器走通用回退。
 * 整体重建模式（pi 同款 updateDisplay）。
 *
 * 图片内联（pi maybeConvertImagesForKitty/updateDisplay 对位）：结果
 * content 里 ``type:'image'`` 的块用 pi-tui Image 组件内联渲染（终端
 * 不支持图片协议时回退 ``[图片: <mimeType>]`` 文本行；kitty+非 PNG
 * 等协议内不可渲染由 Image 组件自带 fallback 文案兜底——无 sharp 依赖，
 * 不做格式转换）。
 */

import type { ContentBlock, NovaUIRuntime } from 'nova-client';
import { extractText, isComponentOutput } from 'nova-client';
import type { ToolCallItem } from '../../../../protocol/nova-wire.gen.js';
import { Box, Container, Image, Spacer, Text, getCapabilities, type Component } from '@earendil-works/pi-tui';

import { blocksToComponents } from '../../blocks/index.js';
import { colors, markdownTheme } from '../../themes/index.js';
import type { ExpansionState } from './expansion.js';

/** 折叠预览的行数上限（对齐 pi PREVIEW_LINES）。 */
const PREVIEW_LINES = 20;

/** live 计时行的刷新间隔（毫秒——秒数翻面的最小感知粒度）。 */
const ELAPSED_TICK_MS = 250;

/** 计时格式化（本地 3 行——nova-client 无 pretty-ms 依赖，不为此引包）。 */
function formatElapsed(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1).replace(/\.0$/, '')}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${s}s`;
}

/**
 * live 卡片计时行（pi-tui Loader 自转语义的对位——宿主 chrome）。
 *
 * 纪律：**宿主持有、生命周期归卡片**（渲染器是纯函数，不自持定时器——
 * 重建/热重载模型下组件级 interval 在第三方手里必泄漏）。自己持有的
 * interval 只 `setText` 自己一行；秒数未翻面时不碰缓存不请求重绘。
 */
class ElapsedLine extends Text {
  private readonly intervalId: ReturnType<typeof setInterval>;
  private lastText: string;

  constructor(requestRender: () => void, startedAt: number) {
    const initial = `Running… ${formatElapsed(Date.now() - startedAt)}`;
    super(colors.dim(initial), 1, 0);
    this.lastText = initial;
    this.intervalId = setInterval(() => {
      const text = `Running… ${formatElapsed(Date.now() - startedAt)}`;
      if (text === this.lastText) return;
      this.lastText = text;
      this.setText(colors.dim(text));
      requestRender();
    }, ELAPSED_TICK_MS);
  }

  stop(): void {
    clearInterval(this.intervalId);
  }
}

/** 从 args 提取主参数摘要（pi 同款：edit/write/read 取 path，bash 取 command 等）。 */
function argsSummary(args: unknown): string {
  const record =
    typeof args === 'object' && args !== null ? (args as Record<string, unknown>) : {};
  const primary =
    record.path ?? record.file_path ?? record.command ?? record.pattern ?? record.query;
  if (typeof primary === 'string' && primary) {
    return primary.length > 60 ? `${primary.slice(0, 60)}…` : primary;
  }
  return '';
}

/** item.status → 卡片呈现态（pending/running = live；done 成功；其余终态为失败色）。 */
function isLiveStatus(status: ToolCallItem['status']): boolean {
  return status === 'pending' || status === 'running';
}

/**
 * 结果图片 → 组件（pi updateDisplay 的图片段对位）。
 * 终端支持图片协议：每张图 Spacer + Image 内联；不支持：单行
 * ``[图片: <mimeType>]`` 文本清单。data/mimeType 缺一的块跳过。
 */
export function buildImageComponents(content: ContentBlock[] | undefined): Component[] {
  const images = (content ?? []).filter(
    (block): block is ContentBlock & { data: string; mimeType: string } =>
      block.type === 'image' &&
      typeof block.data === 'string' &&
      block.data.length > 0 &&
      typeof block.mimeType === 'string' &&
      block.mimeType.length > 0,
  );
  if (images.length === 0) return [];

  if (!getCapabilities().images) {
    // 无图片协议：文本回退（一行一张，任务约定文案）
    return [
      new Text(images.map((img) => `[图片: ${img.mimeType}]`).join('\n'), 1, 0),
    ];
  }
  const components: Component[] = [];
  for (const img of images) {
    components.push(new Spacer(1));
    components.push(
      new Image(img.data, img.mimeType, { fallbackColor: (s) => colors.toolOutput(s) }),
    );
  }
  return components;
}

export class ToolCardView extends Container {
  /** 执行前预览数据（preview 钩子产出，注入渲染器 input.preview）。 */
  private previewData: unknown;
  /** 当前预览对应的参数指纹（参数变化即失效重算——pi previewArgsKey 对位）。 */
  private previewArgsKey: string | undefined;
  /** 预览计算在飞（防重入）。 */
  private previewPending = false;
  /** live 起点（ElapsedLine 计时基准——卡片自身无时间戳，宿主自建）。 */
  private liveStartedAt: number | undefined;
  /** live 计时行（宿主持有，pi Loader 对位）；非 live 或已 dispose 为 undefined。 */
  private elapsedLine: ElapsedLine | undefined;
  /**
   * 渲染器输入指纹（逐项 === 比对——mapping 是字段级原位赋值，
   * 引用变 = 内容变）。指纹不变则 rebuild 整体跳过：消掉"每条
   * transcript 事件重建所有卡片"的隐形放大器。
   */
  private lastFingerprint: readonly unknown[] | undefined;

  constructor(
    private readonly runtime: NovaUIRuntime,
    private item: ToolCallItem,
    private readonly expansion: ExpansionState,
    private readonly requestRender: () => void,
  ) {
    super();
    this.rebuild();
  }

  update(item: ToolCallItem): void {
    this.item = item;
    this.rebuild();
  }

  /** 移除卡片时停计时行（TranscriptController 出口调用；幂等）。 */
  dispose(): void {
    this.elapsedLine?.stop();
    this.elapsedLine = undefined;
  }

  /**
   * 执行前预览（pi edit.ts renderCall 的 argsComplete 分支对位）：
   * 参数完整（pending + argsComplete）、执行未开始时，调渲染器模块的
   * preview 钩子（异步只读），完成后注入渲染器 input.preview 并重绘。
   * 参数变化则作废重算。
   */
  private maybeComputePreview(): void {
    if (this.item.status !== 'pending' || this.item.argsComplete !== true) return;
    const compute = this.runtime.slots.resolveToolPreview(this.item.tool);
    if (!compute) return;

    const argsKey = JSON.stringify(this.item.args);
    if (this.previewArgsKey !== argsKey) {
      this.previewData = undefined;
      this.previewPending = false;
      this.previewArgsKey = argsKey;
    }
    if (this.previewData !== undefined || this.previewPending) return;

    this.previewPending = true;
    const requestKey = argsKey;
    const cwd = this.runtime.store.currentSnapshot?.cwd ?? process.cwd();
    void compute(
      (typeof this.item.args === 'object' && this.item.args !== null
        ? this.item.args
        : {}) as Record<string, unknown>,
      cwd,
    )
      .then((data) => {
        if (this.previewArgsKey !== requestKey) return; // 参数已变，丢弃过期结果
        this.previewData = data;
        this.previewPending = false;
        this.rebuild();
        this.requestRender();
      })
      .catch(() => {
        // 预览失败静默（无预览走通用呈现，不影响执行）
        this.previewPending = false;
      });
  }

  private rebuild(): void {
    // 指纹 memo：输入引用逐项 === 全等且已有子组件 → 整体跳过
    // （item 清单的 delta 应用是字段级原位赋值，引用变 = 内容变）。
    const fingerprint: readonly unknown[] = [
      this.item.status,
      this.item.argsComplete,
      this.item.args,
      this.item.partialResult,
      this.item.result,
      this.previewData,
    ];
    if (
      this.lastFingerprint !== undefined &&
      this.children.length > 0 &&
      fingerprint.every((value, index) => value === this.lastFingerprint![index])
    ) {
      return;
    }
    this.lastFingerprint = fingerprint;

    this.clear();

    const isLive = isLiveStatus(this.item.status);
    if (isLive && this.liveStartedAt === undefined) this.liveStartedAt = Date.now();
    // 计时行实例在 live 期间跨 rebuild 常驻（pi loader 字段对位——一个
    // interval 活完整个 running 期，不随内容区重建生灭）；转非 live 即停。
    if (!isLive && this.elapsedLine !== undefined) {
      this.elapsedLine.stop();
      this.elapsedLine = undefined;
    }

    const bgFn = isLive
      ? colors.toolPendingBg
      : this.item.status === 'done'
        ? colors.toolSuccessBg
        : colors.toolErrorBg;
    const headerBox = new Box(1, 0, bgFn);
    const summary = argsSummary(this.item.args);
    headerBox.addChild(
      new Text(
        colors.toolTitle(this.item.tool) +
          (summary ? colors.dim(` ${summary}`) : '') +
          (this.item.status === 'pending' && !this.item.argsComplete
            ? colors.dim(' …')
            : ''),
        0,
        0,
      ),
    );
    this.addChild(headerBox);

    // 执行前预览：pending + argsComplete 时发起（可能在结果到达前完成注入）
    this.maybeComputePreview();

    const renderer = this.runtime.slots.resolveToolRenderer(this.item.tool);
    const output = renderer?.({
      item: this.item,
      preview: this.previewData,
      env: {
        cwd: this.runtime.store.currentSnapshot?.cwd ?? process.cwd(),
        colors,
        markdownTheme,
        expanded: this.expansion.expanded,
      },
    });

    // 双形态判别：组件直挂（全能力）/ 块列表走适配层（可过网）/ 空 → 通用回退
    if (isComponentOutput(output)) {
      this.addChild(output as unknown as Component);
    } else if (Array.isArray(output) && output.length > 0) {
      for (const component of blocksToComponents(output, this.runtime.slots)) {
        this.addChild(component);
      }
    } else {
      // 通用回退（slot 空态）：结果文本 / args 摘要 / 执行中与参数累积占位（折叠预览）
      const resultPart = this.item.result as { content?: ContentBlock[] } | undefined;
      const partialPart = this.item.partialResult as { content?: ContentBlock[] } | undefined;
      const rawText =
        extractText(resultPart?.content) ||
        extractText(partialPart?.content) ||
        (isLive
          ? colors.dim('running…')
          : colors.dim(JSON.stringify(this.item.args).slice(0, 200)));
      this.addChild(new Text(this.collapseText(rawText), 1, 0));
    }

    // 结果图片内联（渲染器块与通用回退共用——pi updateDisplay 图片段对位）
    const resultContent = (this.item.result as { content?: ContentBlock[] } | undefined)?.content;
    for (const component of buildImageComponents(resultContent)) {
      this.addChild(component);
    }

    // live 计时行（宿主 chrome——pi Loader 对位：组件自转一行，父卡不随之重建；
    // live 期间同一实例跨 rebuild 常驻，转非 live 时已在上方停掉）
    if (isLive && this.liveStartedAt !== undefined) {
      if (this.elapsedLine === undefined) {
        this.elapsedLine = new ElapsedLine(this.requestRender, this.liveStartedAt);
      }
      this.addChild(this.elapsedLine);
    }
    this.addChild(new Spacer(1));
  }

  /** 长输出折叠：未展开时只显示末 PREVIEW_LINES 行（pi 同款预览语义）。 */
  private collapseText(text: string): string {
    if (this.expansion.expanded) return text;
    const lines = text.split('\n');
    if (lines.length <= PREVIEW_LINES) return text;
    const hidden = lines.length - PREVIEW_LINES;
    return [
      colors.dim(`... ${hidden} more lines (ctrl+o to expand)`),
      ...lines.slice(-PREVIEW_LINES),
    ].join('\n');
  }
}
