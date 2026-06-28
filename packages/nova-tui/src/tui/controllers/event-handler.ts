/**
 * Agent 事件处理器。
 * 将 Python 子进程发来的 JSON-RPC notification 转换为 UI 更新。
 */

import type { AgentEvent } from '../rpc-client.js';
import type { StreamingUIController } from './streaming-ui.js';
import type { TranscriptController } from './transcript.js';

function extractToolResultText(raw: unknown): string {
  if (typeof raw === 'string') return raw;
  if (raw === null || raw === undefined) return '';
  if (typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    // AgentToolResult shape: { content: [{ type: 'text', text: '...' }], details: {} }
    const content = obj.content;
    if (Array.isArray(content)) {
      const texts: string[] = [];
      for (const item of content) {
        if (typeof item !== 'object' || item === null) continue;
        const c = item as Record<string, unknown>;
        if (c.type === 'text' && typeof c.text === 'string') {
          texts.push(c.text);
        }
      }
      if (texts.length > 0) return texts.join('\n');
    }
    if (typeof obj.text === 'string') return obj.text;
    if (typeof obj.output === 'string') return obj.output;
  }
  try {
    return JSON.stringify(raw);
  } catch {
    return String(raw);
  }
}

function extractTokenStats(msg: Record<string, unknown>) {
  const usage = msg.usage as Record<string, unknown> | undefined;
  if (!usage) return undefined;
  const input = typeof usage.input === 'number' ? usage.input : 0;
  const output = typeof usage.output === 'number' ? usage.output : 0;
  const cacheRead = typeof usage.cache_read === 'number' ? usage.cache_read : undefined;
  const cacheWrite = typeof usage.cache_write === 'number' ? usage.cache_write : undefined;
  const totalTokens = typeof usage.total_tokens === 'number' ? usage.total_tokens : undefined;
  // If the API provided a usage object but all token counts are zero, treat it as no data.
  if (input === 0 && output === 0 && (totalTokens === undefined || totalTokens === 0)) {
    return undefined;
  }
  return { input, output, cacheRead, cacheWrite, totalTokens };
}

export class EventHandlerController {
  constructor(
    private readonly streaming: StreamingUIController,
    private readonly transcript: TranscriptController,
  ) {}

  handleEvent(evt: AgentEvent): void {
    const type = evt.type;

    switch (type) {
      case 'agent_start':
        this.streaming.setPhase('waiting');
        break;

      case 'message_start': {
        const msg = (evt.message ?? {}) as Record<string, unknown>;
        if (msg.role === 'assistant') {
          this.streaming.beginAssistantMessage();
        }
        break;
      }

      case 'message_update': {
        const ame = (evt.assistant_message_event ?? {}) as Record<string, unknown>;
        const delta = ame.delta as string | undefined;
        if (typeof delta === 'string') {
          if (ame.type === 'text_delta') {
            this.streaming.appendAssistantText(delta);
          } else if (ame.type === 'thinking_delta') {
            this.streaming.appendThinkingText(delta);
          }
        }
        break;
      }

      case 'message_end': {
        const msg = (evt.message ?? {}) as Record<string, unknown>;
        if (msg.role === 'assistant') {
          let text = '';
          let thinking = '';
          const content = msg.content;
          if (Array.isArray(content)) {
            text = content
              .filter((c: unknown) => (c as Record<string, unknown>).type === 'text')
              .map((c: unknown) => (c as Record<string, unknown>).text as string)
              .join('');
            thinking = content
              .filter((c: unknown) => (c as Record<string, unknown>).type === 'thinking')
              .map((c: unknown) => (c as Record<string, unknown>).thinking as string)
              .join('');
          } else if (typeof content === 'string') {
            text = content;
          }
          const tokenStats = extractTokenStats(msg);
          // 空字符串传 undefined，避免 endAssistantMessage 把已流式追加的内容清空
          this.streaming.endAssistantMessage(text || undefined, thinking || undefined, tokenStats);

          const error = msg.error_message as string | undefined;
          if (error) {
            this.transcript.showError(error);
          }
        }
        break;
      }

      case 'tool_execution_start': {
        const toolCallId = (evt.tool_call_id as string) || '';
        const toolName = (evt.tool_name as string) || 'tool';
        const args = (evt.args as Record<string, unknown>) || {};
        this.streaming.beginToolCall(toolCallId, toolName, args);
        break;
      }

      case 'tool_execution_update':
        // 暂不处理增量更新
        break;

      case 'tool_execution_end': {
        const toolCallId = (evt.tool_call_id as string) || '';
        const result = extractToolResultText(evt.result);
        const isError = !!evt.is_error;
        this.streaming.endToolCall(toolCallId, result, isError);
        break;
      }

      case 'turn_start':
        this.streaming.setPhase('waiting');
        break;

      case 'turn_end':
        this.streaming.finalizePendingTools('turn ended');
        this.streaming.setPhase('idle');
        break;

      case 'agent_end':
        this.streaming.reset();
        break;

      case 'auto_compaction_start':
        this.transcript.showStatus('Compacting context…', '#e5c07b');
        break;

      case 'auto_compaction_end':
        this.transcript.showStatus('Compaction done.', '#98c379');
        break;

      default:
        break;
    }
  }
}
