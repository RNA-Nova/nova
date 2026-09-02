/**
 * 呈现模型类型：mirror（会话镜像）的"可渲染"词汇。
 *
 * 架构 2.0：Python 运行时只发原始事件（{type, data} 哑管道），
 * 呈现建模（delta 计算、工具卡片状态、spinner 状态）全部归本层。
 *
 * 类型分层纪律（设计 v3 §14）：
 * - 契约形状（快照/条目/事件）→ 一律 re-export 生成类型，手写即双源；
 * - 本文件只装呈现自有词汇（卡片/条目/状态）。
 */

import type { SessionStateResult } from '../protocol/nova-wire.gen.js';

/** 会话快照 = getSessionState 的契约形状（R8：单一出处，re-export 生成类型）。 */
export type SessionSnapshot = SessionStateResult;

/** 会话工作状态（由 compaction/retry/turn 事件推导，前端画 spinner 用）。 */
export type SessionStatus = 'idle' | 'working' | 'compacting' | 'retrying';

/** 文本/思考/图片等内容块（镜像运行时内容类型的线形状）。 */
export interface ContentBlock {
  type: string;
  text?: string;
  data?: string;
  mimeType?: string;
  [key: string]: unknown;
}

/** 工具调用卡片：一次工具调用的完整可渲染状态。 */
export interface ToolCallCard {
  callId: string;
  toolName: string;
  args: Record<string, unknown>;
  /**
   * 两阶段生命周期（pi 同款）：
   * - `streaming`：参数流式累积中（message_update 从 assistant content 的
   *   toolCall 块建卡，参数逐段累积可见）；
   * - `running`：执行中（tool_execution_start）；
   * - `done`/`error`：执行完结。
   */
  status: 'streaming' | 'running' | 'done' | 'error';
  /**
   * 参数已完整（assistant message_end 时置位）——"参数完整、执行未开始"
   * 窗口的标记：edit 类工具的执行前只读预览（diff）在这个时点触发。
   */
  argsComplete?: boolean;
  /** 流式中间输出（tool_execution_update 的 partial_result）。 */
  partial?: { content?: ContentBlock[]; details?: unknown };
  /** 最终结果（tool_execution_end 的 result，或 abort 收尾的错误文本）。 */
  result?: { content?: ContentBlock[]; details?: unknown };
}

/** transcript 条目（呈现模型，由原始事件/历史条目装配）。 */
export type TranscriptEntry =
  | { kind: 'user'; id: string; text: string }
  | {
      kind: 'assistant';
      id: string;
      text: string;
      /** 思考块内容（独立字段——渲染为斜体暗色区块，不与正文混排）。 */
      thinking?: string;
      /** 终止原因（length/aborted/error 时前端呈现错误行）。 */
      stopReason?: string;
      /** 错误详情（stopReason 为 aborted/error 时的补充文本）。 */
      errorMessage?: string;
      streaming: boolean;
    }
  | { kind: 'toolCall'; id: string; card: ToolCallCard }
  | { kind: 'notice'; id: string; level: 'info' | 'error'; text: string }
  | { kind: 'custom'; id: string; customType: string; data: unknown };

/** MirrorStore 的变更通知负载。 */
export interface StoreChange {
  /** 哪一部分变了。 */
  area: 'transcript' | 'status' | 'snapshot' | 'queue';
}
