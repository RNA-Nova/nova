/**
 * mirror 映射层（终态）：item 三帧应用 + 域通知推导 + 视图派生。
 *
 * 与旧版的根本区别：**不做归约**。"事件 → 呈现模型"的翻译在服务器侧
 * （server/reduction）完成；本层只剩：
 * - item 清单的 apply（started 追加 / delta 合并 / completed 替换）；
 * - 域通知 → 工作状态/快照直写/本地通知（发生系——无呈现重建）；
 * - item 清单 + 本地通知 → transcript 视图条目的纯函数派生。
 *
 * delta 合并规则与后端 ``server/reduction/mapping.apply_delta`` 同一语义：
 * ``text``/``output`` 流式文本字段追加、其余替换（按字段白名单不按值类型——
 * status 等枚举字符串不会被误拼）。
 */

import type {
  AgentMessageItem,
  NovaEventEnvelope,
  NovaWireItem,
  ThinkingItem,
  ToolCallItem,
  UserMessageItem,
} from '../protocol/nova-wire.gen.js';
import type { ContentBlock, NoticeEntry, SessionStatus, TranscriptEntry } from './types.js';

/** 追加语义的流式文本字段（与后端 _DELTA_APPEND_KEYS 同源约定）。 */
const DELTA_APPEND_KEYS = new Set(['text', 'output']);

export interface TranscriptState {
  /** item 清单（服务器归约成品的镜像副本——本层唯一的会话内容状态）。 */
  items: NovaWireItem[];
  /** 本地通知（扩展错误/cache miss/启动提醒——非会话内容）。 */
  notices: NoticeEntry[];
  status: SessionStatus;
  retryStatus: { attempt: number; maxAttempts: number; delayMs: number } | null;
  compactionReason: string | null;
  lastRetryAttempt: number;
}

export function createTranscriptState(): TranscriptState {
  return {
    items: [],
    notices: [],
    status: 'idle',
    retryStatus: null,
    compactionReason: null,
    lastRetryAttempt: 0,
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

/** item/delta 应用：text/output 字符串追加、其余替换（前后端同一语义）。 */
export function applyItemDelta(target: NovaWireItem, delta: Record<string, unknown>): void {
  const targetRecord = target as unknown as Record<string, unknown>;
  for (const [key, value] of Object.entries(delta)) {
    const current = targetRecord[key];
    if (
      DELTA_APPEND_KEYS.has(key) &&
      typeof current === 'string' &&
      typeof value === 'string'
    ) {
      targetRecord[key] = current + value;
    } else {
      targetRecord[key] = value;
    }
  }
}

/** item/started：追加（同 id 幂等替换——重放/重复帧防御）。 */
function applyItemStarted(state: TranscriptState, item: NovaWireItem): boolean {
  const index = state.items.findIndex((existing) => existing.id === item.id);
  if (index >= 0) {
    state.items[index] = item;
  } else {
    state.items.push(item);
  }
  return true;
}

/** item/completed：整件替换（未见 started 则按尾插——防御性）。 */
function applyItemCompleted(state: TranscriptState, item: NovaWireItem): boolean {
  const index = state.items.findIndex((existing) => existing.id === item.id);
  if (index >= 0) {
    state.items[index] = item;
  } else {
    state.items.push(item);
  }
  return true;
}

let noticeSeq = 0;

function pushNotice(state: TranscriptState, level: NoticeEntry['level'], text: string): void {
  noticeSeq += 1;
  state.notices.push({ id: `notice-${noticeSeq}`, level, text, ts: Date.now() });
}

/**
 * 应用一条线上事件到镜像状态。返回是否有可见变更。
 * 内容事件已被服务器归约消亡——本函数只认 item 三帧与域通知。
 */
export function applyRuntimeEvent(state: TranscriptState, event: NovaEventEnvelope): boolean {
  switch (event.type) {
    case 'item_started':
      return applyItemStarted(state, event.data.item);
    case 'item_delta': {
      const item = state.items.find((existing) => existing.id === event.data.id);
      if (item === undefined) return false;
      applyItemDelta(item, event.data.delta);
      return true;
    }
    case 'item_completed':
      return applyItemCompleted(state, event.data.item);

    case 'agent_start':
    case 'turn_start':
      state.status = 'working';
      return true;

    case 'agent_end':
      state.status = 'idle';
      return true;

    case 'compaction_start':
    case 'auto_compaction_start':
      state.status = 'compacting';
      state.compactionReason =
        typeof event.data.reason === 'string' ? event.data.reason : null;
      return true;

    case 'compaction_end':
    case 'auto_compaction_end':
      // 后续状态取决于是否接重试：will_retry（overflow 错误恢复）→ 继续 working；
      // 其余（手动压缩、threshold、overflow 成功）→ run 已结束回 idle。
      state.status = event.data.willRetry === true ? 'working' : 'idle';
      state.compactionReason = null;
      return true;

    case 'auto_retry_start':
      state.status = 'retrying';
      state.lastRetryAttempt =
        typeof event.data.attempt === 'number' ? event.data.attempt : state.lastRetryAttempt;
      state.retryStatus = {
        attempt: typeof event.data.attempt === 'number' ? event.data.attempt : 0,
        maxAttempts:
          typeof event.data.maxAttempts === 'number' ? event.data.maxAttempts : 0,
        delayMs: typeof event.data.delayMs === 'number' ? event.data.delayMs : 0,
      };
      return true;

    case 'auto_retry_end':
      state.status = 'working';
      state.retryStatus = null;
      return true;

    case 'extension_error': {
      // 扩展错误必须可见——silent failure 是排障地狱
      const errorText =
        typeof event.data.error === 'string' ? event.data.error : '未知扩展错误';
      const where = typeof event.data.event === 'string' ? `（${event.data.event}）` : '';
      pushNotice(state, 'error', `扩展错误${where}：${errorText}`);
      return true;
    }

    case 'cache_miss': {
      // pi addCacheMissNotice 对位：显著阈值（2 万 tokens 或 $0.1）才提醒
      const missedTokens =
        typeof event.data.missedTokens === 'number' ? event.data.missedTokens : 0;
      const missedCost =
        typeof event.data.missedCost === 'number' ? event.data.missedCost : 0;
      if (missedTokens < 20_000 && missedCost < 0.1) return false;

      const cost = missedCost >= 0.01 ? `（约 $${missedCost.toFixed(2)}）` : '';
      let label = '缓存 miss';
      if (event.data.modelChanged === true) {
        label = '缓存 miss（模型切换后）';
      } else if (
        typeof event.data.idleMs === 'number' &&
        event.data.idleMs >= 5 * 60 * 1000 // CACHE_TTL_MS（Anthropic 默认 5 分钟）
      ) {
        label = `缓存 miss（空闲 ${Math.round(event.data.idleMs / 60_000)} 分钟后）`;
      }
      pushNotice(state, 'info', `${label}：${formatTokens(missedTokens)} tokens 被重新计费${cost}`);
      return true;
    }

    default:
      // 其余域通知（model_changed / queue_update / session_info_changed 等）
      // 由 store 直写快照层处理；未知事件类型向前兼容，静默忽略
      return false;
  }
}

function formatTokens(count: number): string {
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
}

// ---------------------------------------------------------------------------
// 视图派生（item 清单 + 本地通知 → transcript 条目，纯函数）
// ---------------------------------------------------------------------------

/** assistant 文本块的错误/中止呈现映射（pi 文案对位：aborted 恒有文案）。 */
function assistantStopPresentation(
  item: { status?: string | null; error?: string | null },
  state: TranscriptState,
): { stopReason?: string; errorMessage?: string } {
  if (item.status === 'cancelled') {
    const retried =
      state.lastRetryAttempt > 0 ? `第 ${state.lastRetryAttempt} 次重试后中止` : '已中止';
    return { stopReason: 'aborted', errorMessage: item.error ?? retried };
  }
  if (item.status === 'failed') {
    return { stopReason: 'error', errorMessage: item.error ?? undefined };
  }
  return {};
}

function textOfContent(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return (content as ContentBlock[])
    .filter((block) => block?.type === 'text')
    .map((block) => block.text ?? '')
    .join('');
}

/** 单个 item → transcript 视图条目（不进视图的 item 返回 undefined）。

框架变体按字面判别；CustomItem 的 ``type: string`` 开放成员会打断 TS 的
判别窄化，故各分支显式断言到具体变体（类型层窄化，运行时形状由后端保证）。
 */
export function itemToEntry(item: NovaWireItem, state: TranscriptState): TranscriptEntry | undefined {
  switch (item.type) {
    case 'userMessage': {
      const userItem = item as UserMessageItem;
      return { kind: 'user', id: userItem.id, text: textOfContent(userItem.content) };
    }
    case 'agentMessage': {
      const agentItem = item as AgentMessageItem;
      const stop = assistantStopPresentation(agentItem, state);
      return {
        kind: 'assistant',
        id: agentItem.id,
        text: agentItem.text,
        streaming: agentItem.status === 'running',
        stopReason: stop.stopReason,
        errorMessage: stop.errorMessage,
      };
    }
    case 'thinking': {
      const thinkingItem = item as ThinkingItem;
      return {
        kind: 'assistant',
        id: thinkingItem.id,
        text: '',
        thinking: thinkingItem.text,
        streaming: thinkingItem.status === 'running',
      };
    }
    case 'toolCall':
      return { kind: 'toolCall', id: item.id, item: item as ToolCallItem };
    case 'compaction':
      return { kind: 'custom', id: item.id, customType: 'compaction', data: item };
    case 'branchSummary':
      return { kind: 'custom', id: item.id, customType: 'branch_summary', data: item };
    default:
      // 包级变体（bashExecution 等）：entry:<type> 槽消费 item 本体
      return { kind: 'custom', id: item.id, customType: item.type, data: item };
  }
}

/** store.entries 的来源：item 派生条目 + 本地通知按 ts 归并（稳定序）。 */
export function deriveEntries(state: TranscriptState): TranscriptEntry[] {
  const derived: Array<{ ts: number; entry: TranscriptEntry }> = [];
  for (const item of state.items) {
    const entry = itemToEntry(item, state);
    if (entry !== undefined) derived.push({ ts: item.ts || 0, entry });
  }
  for (const notice of state.notices) {
    derived.push({
      ts: notice.ts,
      entry: { kind: 'notice', id: notice.id, level: notice.level, text: notice.text },
    });
  }
  derived.sort((a, b) => a.ts - b.ts);
  return derived.map((held) => held.entry);
}
