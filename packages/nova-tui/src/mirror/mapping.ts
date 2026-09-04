/**
 * 呈现映射：原始运行时事件（{type, data}）→ transcript / status 模型更新。
 *
 * 纯函数层：不碰 RPC、不碰进程，方便单测与复用。
 * 事件类型来自线上契约的构建期导出（``protocol/nova-wire.gen.ts``，
 * Python 事件的 model_dump 原形状）；本模块是唯一允许"读懂"这些形状
 * 并做呈现决策的地方（架构 2.0：呈现归 Node 层）。
 *
 * R7：``message_end`` 携带 ``stop_reason = aborted | error`` 时，
 * 所有未完结的工具卡片收尾为 error（ message_end 收尾模式）；
 * ``agent_end`` 兜底再做一次（防漏）。
 */

import type { AgentMessage, NovaEventEnvelope } from '../protocol/nova-wire.gen.js';
import type { ContentBlock, SessionStatus, ToolCallCard, TranscriptEntry } from './types.js';

/** TranscriptBuilder 持有的可变状态。 */
export interface TranscriptState {
  entries: TranscriptEntry[];
  status: SessionStatus;
  /** 当前流式 assistant 条目的 id（message_end 时关闭）。 */
  streamingEntryId: string | null;
  /** 当前流式 assistant 的起始时刻（epoch ms——thinking 时长计算锚点）。 */
  streamingStartedAt: number | null;
  /** 进行中的工具卡片（callId → 条目索引）。 */
  openToolCalls: Map<string, number>;
  /** 进行中的用户工具执行（callId → 条目索引——!bash 流式输出聚合为单卡片）。 */
  openUserTools: Map<string, number>;
  /**
   * 最近一次自动重试的 attempt 计数（auto_retry_start 记录）——
   * abort 文案组装用（pi："Aborted after N retry attempts"）；
   * turn 结束（agent_end / message_end）后归零。
   */
  lastRetryAttempt: number;
  /** 重试详情（auto_retry_start payload）——状态指示器倒计时用；retry 结束清空。 */
  retryStatus: { attempt: number; maxAttempts: number; delayMs: number } | null;
  /** 压缩触发原因（manual/threshold/overflow）——状态指示器文案用；压缩结束清空。 */
  compactionReason: string | null;
}

export function createTranscriptState(): TranscriptState {
  return {
    entries: [],
    status: 'idle',
    streamingEntryId: null,
    streamingStartedAt: null,
    openToolCalls: new Map(),
    openUserTools: new Map(),
    lastRetryAttempt: 0,
    retryStatus: null,
    compactionReason: null,
  };
}

let localIdCounter = 0;
function nextId(prefix: string): string {
  localIdCounter += 1;
  return `${prefix}-${localIdCounter}`;
}

/** token 计数的紧凑格式（12345 → "12.3k"）。 */
function _formatTokens(count: number): string {
  if (count >= 1000) return `${(count / 1000).toFixed(1)}k`;
  return String(count);
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

/** 从消息内容提取正文文本（text 块；thinking/工具调用/图片块跳过）。 */
function extractText(message: AgentMessage): string {
  if (!('content' in message)) return '';
  const content = (message as { content?: unknown }).content;
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((block) => {
      if (typeof block === 'object' && block !== null && 'text' in block) {
        const text = (block as { text?: unknown }).text;
        if (typeof text === 'string') return text;
      }
      return '';
    })
    .join('');
}

/** 从消息内容提取思考文本（thinking 块，双换行拼接为多段）。 */
function extractThinking(message: AgentMessage): string {
  if (!('content' in message)) return '';
  const content = (message as { content?: unknown }).content;
  if (!Array.isArray(content)) return '';
  return content
    .map((block) => {
      if (
        typeof block === 'object' &&
        block !== null &&
        (block as { type?: unknown }).type === 'thinking'
      ) {
        const thinking = (block as { thinking?: unknown }).thinking;
        if (typeof thinking === 'string' && thinking.trim()) return thinking.trim();
      }
      return '';
    })
    .filter(Boolean)
    .join('\n\n');
}

/** 消息的角色（标准消息有判别 role；CustomAgentMessage 为不透明对象）。 */
function messageRole(message: AgentMessage): string {
  return 'role' in message ? String(message.role) : '';
}

/** 标准消息角色（custom 分支不得吞掉的集合——toolResult 走工具卡片通道）。 */
const STANDARD_ROLES = new Set(['user', 'assistant', 'system', 'toolResult', 'tool_result']);

/**
 * 判定一条消息是否为 custom 消息（扩展注入 / 用户工具消息）。
 *
 * 线上两族：
 * - role='custom'（CustomMessage——扩展 send_message）：customType 字段判别，
 *   display=false 为上下文注入（不进转录）；
 * - 具名 role（如 'bashExecution'——用户工具消息类）：role 即 customType。
 */
function customMessageParts(
  message: AgentMessage,
): { customType: string; data: unknown; display: boolean } | undefined {
  const role = messageRole(message);
  if (role === 'custom') {
    const record = message as { customType?: unknown; display?: unknown };
    return {
      customType: typeof record.customType === 'string' && record.customType ? record.customType : 'custom',
      data: message,
      display: record.display !== false,
    };
  }
  if (role !== '' && !STANDARD_ROLES.has(role)) {
    return { customType: role, data: message, display: true };
  }
  return undefined;
}

function messageId(message: AgentMessage, fallback: string): string {
  const id = (message as { id?: unknown }).id;
  return typeof id === 'string' && id ? id : fallback;
}

/** assistant 消息的 stop_reason（其他角色返回空串）。 */
function stopReason(message: AgentMessage): string {
  const reason = (message as { stopReason?: unknown }).stopReason;
  return typeof reason === 'string' ? reason : '';
}

function errorMessage(message: AgentMessage): string {
  const text = (message as { errorMessage?: unknown }).errorMessage;
  return typeof text === 'string' && text ? text : '';
}

/** result / partial_result 是工具作者的自由负载（线上为 unknown）。 */
function toolPayload(value: unknown): { content?: ContentBlock[]; details?: unknown } {
  const record = asRecord(value);
  return {
    content: Array.isArray(record.content)
      ? (record.content as ContentBlock[])
      : undefined,
    details: record.details,
  };
}

/** 流式 toolCall 块的呈现视图（assistant content 中 type=toolCall 的块）。 */
interface StreamingToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

/**
 * 从 assistant 消息 content 提取 toolCall 块（pi 两阶段卡片的数据源）。
 * 流式期间 arguments 为部分解析对象（nova_ai 每个 delta 后用
 * parse_streaming_json 增量填充——线一致）。
 */
function extractToolCalls(message: AgentMessage): StreamingToolCall[] {
  if (!('content' in message)) return [];
  const content = (message as { content?: unknown }).content;
  if (!Array.isArray(content)) return [];
  const calls: StreamingToolCall[] = [];
  for (const block of content) {
    const record = asRecord(block);
    if (record.type !== 'toolCall') continue;
    const name = typeof record.name === 'string' ? record.name : '';
    if (!name) continue;
    const id = typeof record.id === 'string' && record.id ? record.id : `streaming-${name}`;
    calls.push({
      id,
      name,
      arguments: asRecord(record.arguments),
    });
  }
  return calls;
}

/**
 * 参数流式期的卡片建/更（pi：message_update 里发现 toolCall 块即建组件、
 * updateArgs 逐段累积）。已完结（done/error）的卡片不回退。
 */
function upsertStreamingToolCard(state: TranscriptState, call: StreamingToolCall): void {
  const existingIndex = state.openToolCalls.get(call.id);
  if (existingIndex !== undefined) {
    const entry = state.entries[existingIndex];
    if (entry?.kind === 'toolCall' && entry.card.status === 'streaming') {
      entry.card.args = call.arguments;
    }
    return;
  }
  const card: ToolCallCard = {
    callId: call.id,
    toolName: call.name,
    args: call.arguments,
    status: 'streaming',
    argsComplete: false,
    startedAt: Date.now(),
  };
  state.openToolCalls.set(call.id, state.entries.length);
  state.entries.push({ kind: 'toolCall', id: call.id, card });
}

/**
 * R7：把所有未完结的工具卡片（参数流式中或执行中）收尾为 error
 * （abort/error 时 turn 中断，卡片不能永远停在 streaming/running）。
 * 返回是否有卡片被收尾。
 */
function finalizeOpenToolCalls(state: TranscriptState, reason: string): boolean {
  let changed = false;
  for (const index of state.openToolCalls.values()) {
    const entry = state.entries[index];
    if (
      entry?.kind === 'toolCall' &&
      (entry.card.status === 'running' || entry.card.status === 'streaming')
    ) {
      entry.card.status = 'error';
      entry.card.result = { content: [{ type: 'text', text: reason }] };
      changed = true;
    }
  }
  state.openToolCalls.clear();
  return changed;
}

/**
 * 应用一条原始事件到 transcript 状态。
 * 返回是否有可见变更（用于决定是否通知订阅者）。
 */
export function applyRuntimeEvent(state: TranscriptState, event: NovaEventEnvelope): boolean {
  switch (event.type) {
    case 'agent_start':
    case 'turn_start':
      state.status = 'working';
      return true;

    case 'agent_end': {
      state.status = 'idle';
      // 兜底：turn 结束时仍有未完结卡片（异常路径），收尾为 error
      finalizeOpenToolCalls(state, '工具调用未完成（turn 已结束）');
      return true;
    }

    case 'message_start': {
      const message = event.data.message;
      const role = messageRole(message);
      if (role === 'user') {
        state.entries.push({
          kind: 'user',
          id: messageId(message, nextId('user')),
          text: extractText(message),
        });
        return true;
      }
      if (role === 'assistant') {
        const id = messageId(message, nextId('assistant'));
        state.streamingEntryId = id;
        // thinking 时长锚点：wire 消息时间戳优先，缺席取当前时刻
        const ts = (message as { timestamp?: unknown }).timestamp;
        state.streamingStartedAt =
          typeof ts === 'number' && ts > 0 ? ts : Date.now();
        state.entries.push({
          kind: 'assistant',
          id,
          text: extractText(message),
          thinking: extractThinking(message) || undefined,
          streaming: true,
        });
        return true;
      }
      return false;
    }

    case 'message_update': {
      if (state.streamingEntryId === null) return false;
      const entry = state.entries.find(
        (e) => e.kind === 'assistant' && e.id === state.streamingEntryId,
      );
      if (!entry || entry.kind !== 'assistant') return false;
      // 运行时发的是累积消息，直接整段替换（delta diff 由渲染层决定）
      entry.text = extractText(event.data.message);
      entry.thinking = extractThinking(event.data.message) || undefined;
      // 两阶段生命周期：参数流式期即建卡（streaming 态），参数逐段累积可见
      for (const call of extractToolCalls(event.data.message)) {
        upsertStreamingToolCard(state, call);
      }
      return true;
    }

    case 'message_end': {
      const message = event.data.message;
      // custom 消息分支（扩展注入 / 用户工具消息）——优先于流式槽判定：
      // 它们既不关 assistant 槽，也不受槽位状态影响，所见即所得。
      const custom = customMessageParts(message);
      if (custom !== undefined) {
        if (!custom.display) return false; // display=false = 上下文注入，不进转录
        if (custom.customType === 'bashExecution') {
          // bash 用户工具：流式聚合条目已在 → 用最终消息定稿（单卡片流式→完结）
          for (const [callId, index] of state.openUserTools) {
            const openEntry = state.entries[index];
            if (openEntry?.kind === 'custom') {
              openEntry.customType = 'bashExecution';
              openEntry.data = custom.data;
            }
            state.openUserTools.delete(callId);
            return true;
          }
        }
        state.entries.push({
          kind: 'custom',
          id: messageId(message, nextId('custom')),
          customType: custom.customType,
          data: custom.data,
        });
        return true;
      }
      let changed = false;
      // 外来 message_end 不关当前流式槽（流式中途插入的其他消息/角色的 end
      // 误关槽会让后续 message_update 全部丢弃）。两道判定：
      const endId = (message as { id?: unknown }).id;
      const slotId = state.streamingEntryId;
      if (slotId !== null) {
        // ① id 可比对且不匹配（本地生成槽位 id——start 无 id 时的
        //    'assistant-N' 占位——无法比对，不算外来）
        const slotIsLocal = slotId.startsWith('assistant-');
        if (
          !slotIsLocal &&
          typeof endId === 'string' &&
          endId !== '' &&
          endId !== slotId
        ) {
          return false;
        }
        // ② 非 assistant 角色的 end（user/toolResult 等）不关 assistant 槽
        if (messageRole(message) !== 'assistant') {
          return false;
        }
      }
      // ：aborted 且经历过自动重试 → 带重试次数
      const abortedText =
        state.lastRetryAttempt > 0 ? `第 ${state.lastRetryAttempt} 次重试后中止` : '已中止';
      // R7：assistant 消息以 aborted/error 收尾 → 未完结工具卡片标 error
      if (messageRole(message) === 'assistant') {
        const reason = stopReason(message);
        if (reason === 'aborted' || reason === 'error') {
          const text =
            errorMessage(message) ||
            (reason === 'aborted' ? abortedText : '工具调用未完成（出错）');
          changed = finalizeOpenToolCalls(state, text);
        } else {
          // 正常结束：参数完整、执行未开始的窗口开启（—
          // edit 类工具的执行前只读预览在这个时点触发）
          for (const index of state.openToolCalls.values()) {
            const toolEntry = state.entries[index];
            if (toolEntry?.kind === 'toolCall' && toolEntry.card.status === 'streaming') {
              toolEntry.card.argsComplete = true;
              changed = true;
            }
          }
        }
        state.lastRetryAttempt = 0;
      }
      if (state.streamingEntryId === null) return changed;
      const entry = state.entries.find(
        (e) => e.kind === 'assistant' && e.id === state.streamingEntryId,
      );
      if (entry && entry.kind === 'assistant') {
        entry.streaming = false;
        // thinking 折叠摘要数据：时长（start→end）+ 按类聚合的工具纵览
        const endTs = (message as { timestamp?: unknown }).timestamp;
        const endMs = typeof endTs === 'number' && endTs > 0 ? endTs : Date.now();
        if (state.streamingStartedAt !== null) {
          entry.thinkingDurationMs = Math.max(0, endMs - state.streamingStartedAt);
        }
        const toolCounts: Record<string, number> = {};
        for (const call of extractToolCalls(message)) {
          toolCounts[call.name] = (toolCounts[call.name] ?? 0) + 1;
        }
        entry.toolCounts = toolCounts;
        // stop_reason/error_message 透出（abort/error 时前端呈现错误行；
        // pi：aborted 始终有文案——后端 error_message 缺席时按 retry 计数组装）
        const reason = stopReason(message);
        if (reason && reason !== 'stop') {
          entry.stopReason = reason;
          entry.errorMessage =
            errorMessage(message) || (reason === 'aborted' ? abortedText : undefined);
        }
      }
      state.streamingEntryId = null;
      state.streamingStartedAt = null;
      return true;
    }

    case 'tool_execution_start': {
      const callId = event.data.toolCallId || nextId('call');
      // 参数流式期已建卡（pi：组件已存在则只标记进入执行阶段，不动 args）
      const existingIndex = state.openToolCalls.get(callId);
      if (existingIndex !== undefined) {
        const existing = state.entries[existingIndex];
        if (existing?.kind === 'toolCall' && existing.card.status === 'streaming') {
          existing.card.status = 'running';
          return true;
        }
      }
      // 非流式路径（或历史回放）：执行开始才首次见到，直接建 running 卡
      const card: ToolCallCard = {
        callId,
        toolName: event.data.toolName,
        args: asRecord(event.data.args),
        status: 'running',
        argsComplete: true,
      };
      state.openToolCalls.set(callId, state.entries.length);
      state.entries.push({ kind: 'toolCall', id: callId, card });
      return true;
    }

    case 'tool_execution_update': {
      const index = state.openToolCalls.get(event.data.toolCallId);
      if (index === undefined) return false;
      const entry = state.entries[index];
      if (entry?.kind !== 'toolCall') return false;
      entry.card.partial = toolPayload(event.data.partialResult);
      return true;
    }

    case 'tool_execution_end': {
      const index = state.openToolCalls.get(event.data.toolCallId);
      if (index === undefined) return false;
      const entry = state.entries[index];
      if (entry?.kind !== 'toolCall') return false;
      entry.card.status = event.data.isError ? 'error' : 'done';
      entry.card.result = toolPayload(event.data.result);
      state.openToolCalls.delete(event.data.toolCallId);
      return true;
    }

    case 'user_tool': {
      // 流式聚合（!bash 输出块）：同一 callId 的 chunk 累积进单卡片，
      // 最终由 message_end（bashExecution）定稿——不逐块堆条目。
      const callId =
        typeof event.data.callId === 'string' && event.data.callId
          ? event.data.callId
          : nextId('custom');
      let index = state.openUserTools.get(callId);
      if (index === undefined) {
        index = state.entries.length;
        state.openUserTools.set(callId, index);
        // 工具族 customType（bash 用户工具的终态消息型是 bashExecution——
        // 流式期即以同一视图渲染，完结无缝定稿）
        const tool = typeof event.data.tool === 'string' && event.data.tool ? event.data.tool : 'user_tool';
        // start 事件携带命令串（bash 用户工具执行前发射）——流式卡片即刻
        // 渲染 `$ command` 头，避免慢命令的输出先于命令出现
        const payload = asRecord(event.data.data);
        state.entries.push({
          kind: 'custom',
          id: callId,
          customType: tool === 'bash' ? 'bashExecution' : tool,
          data: {
            command: typeof payload.command === 'string' ? payload.command : '',
            output: '',
            excludeFromContext: payload.excludeFromContext === true,
          },
        });
      }
      const entry = state.entries[index];
      if (entry?.kind !== 'custom') return false;
      const chunk = asRecord(event.data.data).chunk;
      if (typeof chunk === 'string') {
        const prev = asRecord(entry.data);
        entry.data = { ...prev, output: `${typeof prev.output === 'string' ? prev.output : ''}${chunk}` };
      }
      return true;
    }

    case 'compaction_start':
    case 'auto_compaction_start':
      state.status = 'compacting';
      // 触发原因记录（状态指示器文案：manual/threshold/overflow）
      state.compactionReason =
        typeof event.data.reason === 'string' ? event.data.reason : null;
      return true;

    case 'compaction_end':
    case 'auto_compaction_end':
      // 后续状态取决于是否接重试：will_retry（overflow 错误恢复）→ 继续 working；
      // 其余（手动压缩、threshold、overflow 成功）→ run 已结束回 idle。
      // 无条件 working 会让失败的手动压缩（无可压缩内容抛错）把状态永远卡住
      state.status = event.data.willRetry === true ? 'working' : 'idle';
      state.compactionReason = null;
      return true;

    case 'auto_retry_start':
      state.status = 'retrying';
      // ：记录次数供 abort 文案组装（turn 结束归零）
      state.lastRetryAttempt =
        typeof event.data.attempt === 'number' ? event.data.attempt : state.lastRetryAttempt;
      // 重试详情（状态指示器倒计时：attempt/maxAttempts/delayMs）
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

    case 'entry_appended': {
      // 仅 custom 条目实时进 transcript（消息/压缩等各有专属通道）
      const appended = asRecord(event.data.entry);
      if (appended.type !== 'custom') return false;
      state.entries.push({
        kind: 'custom',
        id: typeof appended.id === 'string' ? appended.id : nextId('custom'),
        customType:
          typeof appended.customType === 'string' ? appended.customType : 'custom',
        data: appended.data,
      });
      return true;
    }

    case 'extension_error': {
      // 扩展错误必须可见——silent failure 是排障地狱（此前只进事件流不进转录，
      // 命令 handler 崩溃在用户眼里就是"没反应"）
      const errorText =
        typeof event.data.error === 'string' ? event.data.error : '未知扩展错误';
      const where = typeof event.data.event === 'string' ? `（${event.data.event}）` : '';
      state.entries.push({
        kind: 'notice',
        id: nextId('notice'),
        level: 'error',
        text: `扩展错误${where}：${errorText}`,
      });
      return true;
    }

    case 'cache_miss': {
      // ：显著阈值（2 万 tokens 或 $0.1）才提醒；
      // 后端已过噪声地板（1024 tokens），这里是"值得打扰用户"的线
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
      state.entries.push({
        kind: 'notice',
        id: nextId('notice'),
        level: 'info',
        text: `${label}：${_formatTokens(missedTokens)} tokens 被重新计费${cost}`,
      });
      return true;
    }

    default:
      // 其余事件（model_changed / thinking_level_changed / queue_update /
      // session_info_changed 等）不进 transcript——由 store 直写快照层处理；
      // 联合之外的新事件类型（更新的后端）向前兼容，静默忽略
      return false;
  }
}
