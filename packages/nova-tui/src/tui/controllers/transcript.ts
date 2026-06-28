/**
 * 消息历史（Transcript）控制器。
 * 负责消息组件的创建、追加、替换和清空。
 */

import type { Component } from '@earendil-works/pi-tui';
import { Spacer } from '@earendil-works/pi-tui';

import { AssistantMessageComponent } from '../components/assistant-message.js';
import { StatusMessageComponent } from '../components/status-message.js';
import { ThinkingComponent } from '../components/thinking.js';
import { ToolCallComponent } from '../components/tool-call.js';
import { UserMessageComponent } from '../components/user-message.js';
import type { TUIState, TranscriptEntry } from '../state.js';

export interface HistoryMessage {
  role: 'user' | 'assistant' | 'toolResult';
  content?: string | Array<Record<string, unknown>>;
  tool_call_id?: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  is_error?: boolean;
  timestamp?: number;
}

function extractTextFromContent(
  content: string | Array<Record<string, unknown>> | undefined,
  includeThinking = false,
): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  const items = content.filter(
    (c): c is Record<string, unknown> => typeof c === 'object' && c !== null,
  );
  const texts = items
    .filter((c) => c.type === 'text')
    .map((c) => (typeof c.text === 'string' ? c.text : ''))
    .join('');
  if (texts) return texts;
  if (includeThinking) {
    const thinking = items
      .filter((c) => c.type === 'thinking')
      .map((c) => (typeof c.thinking === 'string' ? c.thinking : ''))
      .join('\n');
    return thinking;
  }
  return '';
}

export class TranscriptController {
  constructor(private readonly state: TUIState) {}

  appendEntry(entry: TranscriptEntry, component?: Component): void {
    const prevEntry = this.state.transcriptEntries.at(-1);
    this.state.transcriptEntries.push(entry);
    const comp = component ?? this.createComponent(entry);
    if (comp) {
      // 在消息之间添加统一间距，但连续 tool_call 之间保持紧凑
      if (
        this.state.transcriptEntries.length > 1 &&
        !(prevEntry?.kind === 'tool_call' && entry.kind === 'tool_call')
      ) {
        this.state.transcriptContainer.addChild(new Spacer(1));
      }
      this.state.transcriptContainer.addChild(comp);
      this.state.ui.requestRender();
    }
  }

  replaceLastTool(component: Component): void {
    const children = this.state.transcriptContainer.children;
    for (let i = children.length - 1; i >= 0; i--) {
      if (children[i] instanceof ToolCallComponent) {
        this.state.transcriptContainer.removeChild(children[i]);
        this.state.transcriptContainer.addChild(component);
        this.state.ui.requestRender();
        return;
      }
    }
    this.state.transcriptContainer.addChild(component);
    this.state.ui.requestRender();
  }

  clear(): void {
    this.state.transcriptEntries = [];
    this.state.transcriptContainer.clear();
    this.state.nextEntryId = 1;
    this.state.ui.requestRender();
  }

  showStatus(message: string, color?: string): void {
    this.appendEntry({
      id: this.state.nextEntryId++,
      kind: 'status',
      content: message,
      color,
    });
  }

  showError(message: string): void {
    this.showStatus(message, '#e06c75');
  }

  nextId(): number {
    return this.state.nextEntryId++;
  }

  /**
   * 加载并渲染会话历史消息。会清空现有 transcript 内容。
   */
  loadHistory(messages: HistoryMessage[]): void {
    if (messages.length === 0) return;

    // 保留之前的条目，仅移除欢迎/占位组件：清空 transcriptContainer 并重建
    this.state.transcriptEntries = [];
    this.state.transcriptContainer.clear();
    this.state.nextEntryId = 1;

    for (const msg of messages) {
      const id = this.state.nextEntryId++;
      if (msg.role === 'user') {
        const text = extractTextFromContent(msg.content);
        if (!text) continue;
        const entry: TranscriptEntry = { id, kind: 'user', content: text };
        this.appendEntry(entry, new UserMessageComponent(text));
      } else if (msg.role === 'assistant') {
        const text = extractTextFromContent(msg.content, true);
        if (!text) continue;
        const entry: TranscriptEntry = { id, kind: 'assistant', content: text };
        this.appendEntry(entry, new AssistantMessageComponent(text));
      } else if (msg.role === 'toolResult') {
        const resultText = extractTextFromContent(msg.content);
        const toolName = msg.tool_name || 'tool';
        const toolArgs = msg.tool_args || {};
        const entry: TranscriptEntry = {
          id,
          kind: 'tool_call',
          content: resultText,
          toolName,
          toolArgs,
          toolResult: resultText,
          toolError: !!msg.is_error,
        };
        this.appendEntry(entry, new ToolCallComponent(toolName, toolArgs, resultText, !!msg.is_error));
      }
    }
  }

  createComponent(entry: TranscriptEntry): Component | null {
    switch (entry.kind) {
      case 'user':
        return new UserMessageComponent(entry.content);
      case 'assistant':
        return new AssistantMessageComponent(entry.content);
      case 'tool_call':
        return new ToolCallComponent(
          entry.toolName || 'tool',
          entry.toolArgs || {},
          entry.toolResult,
          entry.toolError,
        );
      case 'thinking':
        return new ThinkingComponent(entry.content, this.state.ui);
      case 'status':
        return new StatusMessageComponent(entry.content, entry.color, entry.detail);
      default:
        return null;
    }
  }
}
