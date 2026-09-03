/**
 * MirrorStore：会话镜像（mirror）的状态容器。
 *
 * 数据源两个：
 * - 快照 + 历史条目（连接/恢复时经 RPC 全量拉取，``sync``）；
 * - 原始事件流（之后增量驱动，经 mapping 呈现映射，``apply``）。
 *
 * bus 关系：mirror 是 bus 的**特权订阅者**（按序、恰好一次、先于观察者），
 * 并在状态迁移确定后发布派生便利事件（``session:synced`` / ``turn:*``）。
 *
 * 快照维护纪律：
 * - payload 即完整事实的事件**直写**（model_changed / thinking_level_changed /
 *   session_info_changed / queue_update）——省一趟 pull，且不是推测；
 * - 其余会影响快照的事件**不猜增量语义**，由前端需要时经 RPC 重拉。
 */

import type { NovaBus } from '../bus.js';
import type { ModelRef, NovaEventEnvelope } from '../protocol/nova-wire.gen.js';
import { applyRuntimeEvent, createTranscriptState, type TranscriptState } from './mapping.js';
import type {
  ContentBlock,
  SessionSnapshot,
  SessionStatus,
  StoreChange,
  TranscriptEntry,
} from './types.js';

/** 历史条目（getSessionEntries）中 message 条目的线形状。 */
export interface HistoryEntry {
  id: string;
  parentId?: string | null;
  type: string;
  message?: Record<string, unknown>;
  [key: string]: unknown;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

/** 窄化为 ModelRef（model_changed 的 payload 线上类型为 unknown，防御性校验）。 */
function asModelRef(value: unknown): ModelRef | null | undefined {
  if (value === null) return null;
  const record = asRecord(value);
  if (typeof record.provider === 'string' && typeof record.id === 'string') {
    return { provider: record.provider, id: record.id };
  }
  return undefined;
}

export class MirrorStore {
  private transcript: TranscriptState = createTranscriptState();
  private snapshot: SessionSnapshot | null = null;
  private listeners = new Set<(change: StoreChange) => void>();
  private detachMirror: (() => void) | undefined;

  constructor(private readonly bus?: NovaBus) {
    // mirror 特权订阅：先于一切观察者应用事件，保证状态正确性
    this.detachMirror = bus?.subscribeMirror((event) => this.apply(event));
  }

  /** 脱离 bus（关停/重建时）。 */
  dispose(): void {
    this.detachMirror?.();
    this.detachMirror = undefined;
  }

  /** 订阅呈现模型变更；返回退订函数。 */
  subscribe(listener: (change: StoreChange) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(area: StoreChange['area']): void {
    for (const listener of this.listeners) listener({ area });
  }

  get entries(): readonly TranscriptEntry[] {
    return this.transcript.entries;
  }

  get status(): SessionStatus {
    return this.transcript.status;
  }

  /** 重试详情（auto_retry_start 直写；retry 结束清空）——状态指示器倒计时数据源。 */
  get retryStatus(): { attempt: number; maxAttempts: number; delayMs: number } | null {
    return this.transcript.retryStatus;
  }

  /** 压缩触发原因（manual/threshold/overflow；压缩结束清空）。 */
  get compactionReason(): string | null {
    return this.transcript.compactionReason;
  }

  get currentSnapshot(): SessionSnapshot | null {
    return this.snapshot;
  }

  /** 全量同步：连接/恢复时以快照 + 历史条目重建状态。 */
  sync(snapshot: SessionSnapshot, history: HistoryEntry[]): void {
    this.snapshot = snapshot;
    this.transcript = createTranscriptState();
    for (const entry of history) {
      // 非消息条目：压缩/分支摘要/自定义条目 → custom 条目（历史不丢）
      if (entry.type === 'compaction') {
        this.transcript.entries.push({
          kind: 'custom',
          id: entry.id,
          customType: 'compaction',
          data: asRecord(entry),
        });
        continue;
      }
      if (entry.type === 'branch_summary') {
        this.transcript.entries.push({
          kind: 'custom',
          id: entry.id,
          customType: 'branch_summary',
          data: asRecord(entry),
        });
        continue;
      }
      if (entry.type === 'custom' || entry.type === 'custom_message') {
        const record = asRecord(entry);
        this.transcript.entries.push({
          kind: 'custom',
          id: entry.id,
          customType: String(record.customType ?? 'custom'),
          data: record,
        });
        continue;
      }
      if (entry.type !== 'message' || !entry.message) continue;
      const message = asRecord(entry.message);
      const role = String(message.role ?? '');
      if (role === 'user') {
        this.transcript.entries.push({
          kind: 'user',
          id: entry.id,
          text: extractHistoryText(message),
        });
      } else if (role === 'assistant') {
        const stopReason = String(message.stopReason ?? '');
        this.transcript.entries.push({
          kind: 'assistant',
          id: entry.id,
          text: extractHistoryText(message),
          thinking: extractHistoryThinking(message),
          stopReason: stopReason && stopReason !== 'stop' ? stopReason : undefined,
          errorMessage:
            typeof message.errorMessage === 'string'
              ? message.errorMessage
              : undefined,
          streaming: false,
        });
      } else if (role === 'toolResult') {
        this.transcript.entries.push({
          kind: 'toolCall',
          id: entry.id,
          card: {
            callId: String(message.toolCallId ?? entry.id),
            toolName: String(message.toolName ?? ''),
            args: {},
            status: message.isError === true ? 'error' : 'done',
            result: {
              content: Array.isArray(message.content)
                ? (message.content as ContentBlock[])
                : undefined,
              details: message.details,
            },
          },
        });
      } else if (role === 'bashExecution' || role === 'custom') {
        // 用户工具消息（bashExecution）与扩展 custom 消息 → custom 条目
        this.transcript.entries.push({
          kind: 'custom',
          id: entry.id,
          customType: role === 'bashExecution' ? 'bashExecution' : String(message.customType ?? 'custom'),
          data: message,
        });
      }
      // 其余 role（system 等）不进 transcript
    }
    this.emit('snapshot');
    this.emit('transcript');
    this.bus?.publishDerived('session:synced', undefined);
  }

  /** 追加一条 notice 条目（启动提醒等瞬态信息）。 */
  addNotice(level: 'info' | 'error', text: string): void {
    this.transcript.entries.push({
      kind: 'notice',
      id: `notice-${String(this.transcript.entries.length + 1)}`,
      level,
      text,
    });
    this.emit('transcript');
  }

  /**
   * 快照补丁写入（命令响应回写通道）：命令的**响应里携带着变更后的事实**
   * （如 setActiveTools → active_tools），由 runtime 薄转发到这里。
   * 纪律不变：只写"响应即完整事实"的字段，不猜。
   */
  updateSnapshot(patch: Partial<SessionSnapshot>): void {
    if (this.snapshot === null) return;
    Object.assign(this.snapshot, patch);
    this.emit('snapshot');
  }

  /** 应用一条原始运行时事件（bus 特权通道的唯一入口）。 */
  apply(event: NovaEventEnvelope): void {
    const prevStatus = this.transcript.status;
    const changed = applyRuntimeEvent(this.transcript, event);
    if (changed) this.emit('transcript');
    // status 迁移通知（OSC 9;4 进度/状态指示器的数据源）——只在实际变化时
    // emit，重复事件（如已 working 再来 agent_start）不引发渲染风暴
    if (this.transcript.status !== prevStatus) this.emit('status');

    // 派生便利事件（状态迁移确定后发布）
    if (event.type === 'agent_start') this.bus?.publishDerived('turn:started', undefined);
    if (event.type === 'agent_end') this.bus?.publishDerived('turn:ended', undefined);

    // 快照直写：payload 即完整事实的四类事件 + 生命周期布尔（事件即状态迁移
    // 本身——compaction_start 就是"开始压缩"，无推测成分）
    if (this.snapshot === null) return;
    switch (event.type) {
      case 'model_changed': {
        const model = asModelRef(event.data.model);
        if (model !== undefined) {
          this.snapshot.model = model;
          this.emit('snapshot');
        }
        break;
      }
      case 'thinking_level_changed': {
        // 与后端 getSessionState 同一语义：null → "off"
        this.snapshot.thinkingLevel = event.data.level ?? 'off';
        this.emit('snapshot');
        break;
      }
      case 'session_info_changed': {
        // payload = 三字段当前全量值（无脑直写，无增量歧义）
        this.snapshot.sessionName = event.data.name;
        if (event.data.agent !== undefined) this.snapshot.agentName = event.data.agent;
        if (event.data.personaOverride !== undefined)
          this.snapshot.personaOverride = event.data.personaOverride;
        this.emit('snapshot');
        break;
      }
      case 'queue_update': {
        this.snapshot.steeringMessages = event.data.steering;
        this.snapshot.followUpMessages = event.data.followUp;
        this.emit('queue');
        break;
      }
      case 'compaction_start':
      case 'auto_compaction_start':
        this.snapshot.isCompacting = true;
        this.emit('snapshot');
        break;
      case 'compaction_end':
      case 'auto_compaction_end':
        this.snapshot.isCompacting = false;
        this.emit('snapshot');
        break;
      case 'auto_retry_start':
        this.snapshot.isRetrying = true;
        this.emit('snapshot');
        break;
      case 'auto_retry_end':
        this.snapshot.isRetrying = false;
        this.emit('snapshot');
        break;
      default:
        break;
    }
  }
}

function extractHistoryText(message: Record<string, unknown>): string {
  const content = message.content;
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .filter(
      (block): block is ContentBlock =>
        typeof block === 'object' && block !== null &&
        String((block as ContentBlock).type) === 'text',
    )
    .map((block) => block.text ?? '')
    .join('');
}

/** 历史消息的 thinking 提取（thinking 块，双换行拼接）。 */
function extractHistoryThinking(
  message: Record<string, unknown>,
): string | undefined {
  const content = message.content;
  if (!Array.isArray(content)) return undefined;
  const parts = content
    .filter(
      (block): block is ContentBlock =>
        typeof block === 'object' && block !== null &&
        String((block as ContentBlock).type) === 'thinking',
    )
    .map((block) => String((block as { thinking?: unknown }).thinking ?? '').trim())
    .filter(Boolean);
  return parts.length > 0 ? parts.join('\n\n') : undefined;
}
