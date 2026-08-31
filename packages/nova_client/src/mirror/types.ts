/**
 * 呈现模型类型：mirror（会话镜像）的"可渲染"词汇。
 *
 * 终态（server-item-layer 设计）：**归约归服务器**——mirror 不再把原始
 * 事件翻译成呈现模型；服务器归约产出的 item 流（item_started/delta/
 * completed）是唯一内容源，本层只做 apply（追加/合并/替换）与视图派生。
 *
 * 类型分层纪律（设计 v3 §14）：
 * - 契约形状（快照/条目/事件/item）→ 一律 re-export 生成类型，手写即双源；
 * - 本文件只装呈现自有词汇（视图条目/状态/通知）。
 */

import type {
  NovaWireItem,
  SessionStateResult,
  ToolCallItem,
} from '../protocol/nova-wire.gen.js';

/** 会话快照 = getSessionState 的契约形状（R8：单一出处，re-export 生成类型）。 */
export type SessionSnapshot = SessionStateResult;

/** 会话工作状态（由 agent/turn/compaction/retry 域通知推导，前端画 spinner 用）。 */
export type SessionStatus = 'idle' | 'working' | 'compacting' | 'retrying';

/** 文本/思考/图片等内容块（镜像运行时内容类型的线形状）。 */
export interface ContentBlock {
  type: string;
  text?: string;
  data?: string;
  mimeType?: string;
  [key: string]: unknown;
}

/** transcript 本地通知（扩展错误/cache miss/启动提醒——非会话内容，不进 item）。 */
export interface NoticeEntry {
  id: string;
  level: 'info' | 'error';
  text: string;
  /** 创建时刻（epoch ms）——与 item 清单按 ts 归并排显。 */
  ts: number;
}

/**
 * transcript 视图条目（store.entries 的派生物——从 item 清单 + 本地通知
 * 纯函数派生，不是状态）。kind 与渲染组件一一对应。
 */
export type TranscriptEntry =
  | { kind: 'user'; id: string; text: string }
  | {
      kind: 'assistant';
      id: string;
      text: string;
      /** 思考块内容（独立字段——渲染为斜体暗色区块，不与正文混排）。 */
      thinking?: string;
      /** 终止/失败呈现（cancelled→中止文案、failed→错误行）。 */
      stopReason?: string;
      errorMessage?: string;
      streaming: boolean;
    }
  | { kind: 'toolCall'; id: string; item: ToolCallItem }
  | { kind: 'notice'; id: string; level: 'info' | 'error'; text: string }
  | { kind: 'custom'; id: string; customType: string; data: unknown };

/** MirrorStore 的变更通知负载。 */
export interface StoreChange {
  /** 哪一部分变了。 */
  area: 'transcript' | 'status' | 'snapshot' | 'queue';
}
