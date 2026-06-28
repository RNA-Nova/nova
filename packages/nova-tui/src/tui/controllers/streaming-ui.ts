/**
 * 流式渲染状态控制器。
 * 管理 activeAssistant、工具调用、streamingPhase 等运行时状态。
 *
 * Thinking 内容不再作为独立 transcript 条目显示，而是动态更新到
 * activityContainer（editor 上方）的 ThinkingActivityComponent 中。
 */

import { AssistantMessageComponent } from '../components/assistant-message.js';
import { ThinkingActivityComponent, type TokenStats } from '../components/thinking-activity.js';
import { ThinkingComponent } from '../components/thinking.js';
import { ToolCallComponent } from '../components/tool-call.js';
import type { TranscriptController } from './transcript.js';
import type { TUIState } from '../state.js';

const FLUSH_INTERVAL_MS = 100;
const THINKING_SUMMARY_MS = 3_000; // How long to keep the "thought · 1.2s · ..." summary visible

export class StreamingUIController {
  private activeAssistant: AssistantMessageComponent | undefined;
  private activeThinkingActivity: ThinkingActivityComponent | undefined;

  // Track all pending tool-call cards keyed by tool_call_id.
  private pendingTools = new Map<string, ToolCallComponent>();

  // Drafts — accumulated deltas waiting to be flushed
  private assistantDraft = '';
  private thinkingDraft = '';
  private pendingAssistantFlush = false;
  private pendingThinkingFlush = false;
  private flushTimer: ReturnType<typeof setTimeout> | undefined;

  // Cancelled — visual abort requested by user (Ctrl+C)
  private cancelled = false;

  // Delayed disposal of thinking activity summary
  private thinkingDisposeTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly state: TUIState,
    private readonly transcript: TranscriptController,
  ) {}

  // ------------------------------------------------------------------
  // Assistant 流式消息
  // ------------------------------------------------------------------
  beginAssistantMessage(): void {
    this.cancelled = false;
    this.state.appState.thinking = false;
    this.state.appState.thinkingText = '';
    this.activeAssistant = new AssistantMessageComponent('');
    this.transcript.appendEntry(
      { id: this.transcript.nextId(), kind: 'assistant', content: '' },
      this.activeAssistant,
    );
    // Switch activity indicator to generating as soon as assistant text starts.
    if (!this.activeThinkingActivity) {
      this.activeThinkingActivity = new ThinkingActivityComponent('', this.state.ui);
      this.state.activityContainer.addChild(this.activeThinkingActivity);
    } else {
      this.cancelPendingDisposal();
    }
    this.activeThinkingActivity.setGenerating();
    this.setPhase('composing');
  }

  // ------------------------------------------------------------------
  // Waiting indicator (shown after user sends a message until the model
  // starts responding).
  // ------------------------------------------------------------------
  beginWaiting(): void {
    if (this.cancelled) return;
    if (!this.activeThinkingActivity) {
      this.activeThinkingActivity = new ThinkingActivityComponent('', this.state.ui);
      this.activeThinkingActivity.setWaiting();
      this.state.activityContainer.addChild(this.activeThinkingActivity);
    } else {
      this.cancelPendingDisposal();
      this.activeThinkingActivity.setWaiting();
    }
    this.setPhase('waiting');
  }

  // ------------------------------------------------------------------
  // Delta accumulation
  // ------------------------------------------------------------------
  appendAssistantText(delta: string): void {
    if (this.cancelled) return;
    this.assistantDraft += delta;
    this.pendingAssistantFlush = true;
    if (this.activeThinkingActivity) {
      this.cancelPendingDisposal();
      this.activeThinkingActivity.setGenerating();
    }
    this.scheduleFlush();
  }

  appendThinkingText(delta: string): void {
    if (this.cancelled) return;
    // Clear any lingering previous thinking summary immediately
    if (this.thinkingDisposeTimer) {
      this.disposeThinkingActivity(true);
    }
    this.thinkingDraft += delta;
    this.pendingThinkingFlush = true;
    this.state.appState.thinking = true;
    if (!this.activeThinkingActivity) {
      this.activeThinkingActivity = new ThinkingActivityComponent(this.thinkingDraft, this.state.ui);
      this.state.activityContainer.addChild(this.activeThinkingActivity);
    } else {
      // Switch from waiting or tool-call mode back to thinking.
      this.activeThinkingActivity.setThinking();
    }
    this.scheduleFlush();
  }

  // ------------------------------------------------------------------
  // Flush
  // ------------------------------------------------------------------
  private scheduleFlush(): void {
    if (this.flushTimer !== undefined) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = undefined;
      this.flush();
    }, FLUSH_INTERVAL_MS);
  }

  private flush(): void {
    if (!this.pendingAssistantFlush && !this.pendingThinkingFlush) return;
    if (this.pendingAssistantFlush && this.activeAssistant) {
      this.activeAssistant.appendContent(this.assistantDraft);
      this.assistantDraft = '';
    }
    if (this.pendingThinkingFlush) {
      this.state.appState.thinkingText = this.thinkingDraft;
      if (this.activeThinkingActivity) {
        this.activeThinkingActivity.setText(this.thinkingDraft);
      }
    }

    this.pendingAssistantFlush = false;
    this.pendingThinkingFlush = false;
    this.state.ui.requestRender();
  }

  flushNow(): void {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = undefined;
    }
    this.flush();
  }

  private cancelPendingDisposal(): void {
    if (this.thinkingDisposeTimer) {
      clearTimeout(this.thinkingDisposeTimer);
      this.thinkingDisposeTimer = undefined;
    }
  }

  private disposeThinkingActivity(immediate = false): void {
    this.cancelPendingDisposal();

    if (!this.activeThinkingActivity) return;

    if (immediate) {
      this.activeThinkingActivity.dispose();
      this.state.activityContainer.removeChild(this.activeThinkingActivity);
      this.activeThinkingActivity = undefined;
      return;
    }

    // Stop the timer/spinner but keep the component visible for a short while
    // so the user can read the final "thought · 1.2s · ..." summary.
    this.activeThinkingActivity.stopTimer();
    this.state.ui.requestRender();

    this.thinkingDisposeTimer = setTimeout(() => {
      this.thinkingDisposeTimer = undefined;
      if (this.activeThinkingActivity) {
        this.activeThinkingActivity.dispose();
        this.state.activityContainer.removeChild(this.activeThinkingActivity);
        this.activeThinkingActivity = undefined;
        this.state.ui.requestRender();
      }
    }, THINKING_SUMMARY_MS);
  }

  // ------------------------------------------------------------------
  // Visual cancel (Ctrl+C) — stops rendering immediately
  // ------------------------------------------------------------------
  cancel(): void {
    if (this.cancelled) return;
    this.cancelled = true;
    this.flushNow();
    if (this.activeAssistant) {
      // Append a cancellation marker to the partially-rendered message
      this.activeAssistant.appendContent('\n\n— cancelled —');
      this.activeAssistant = undefined;
    }
    for (const [id, tool] of this.pendingTools) {
      tool.setResult('cancelled', true);
      tool.dispose();
      this.pendingTools.delete(id);
    }
    this.disposeThinkingActivity(true);
    this.assistantDraft = '';
    this.thinkingDraft = '';
    this.state.appState.thinkingText = '';
    this.state.appState.thinking = false;
    this.pendingAssistantFlush = false;
    this.pendingThinkingFlush = false;
    this.setPhase('idle');
  }

  // ------------------------------------------------------------------
  // End of stream
  // ------------------------------------------------------------------
  endAssistantMessage(text?: string, thinking?: string, tokenStats?: TokenStats): void {
    if (this.cancelled) {
      this.cancelled = false;
      this.activeAssistant = undefined;
      this.assistantDraft = '';
      this.thinkingDraft = '';
      this.state.appState.thinkingText = '';
      this.state.appState.thinking = false;
      this.disposeThinkingActivity(true);
      this.setPhase('idle');
      return;
    }
    this.flushNow();
    if (this.activeAssistant && text !== undefined) {
      this.activeAssistant.updateContent(text);
    } else if (text !== undefined) {
      this.transcript.appendEntry({
        id: this.transcript.nextId(),
        kind: 'assistant',
        content: text,
      });
    }
    this.activeAssistant = undefined;
    this.thinkingDraft = '';
    this.state.appState.thinkingText = '';
    this.state.appState.thinking = false;

    if (this.activeThinkingActivity && tokenStats) {
      this.activeThinkingActivity.setTokenStats(tokenStats);
    }
    this.disposeThinkingActivity();
    this.setPhase('idle');
  }

  // ------------------------------------------------------------------
  // Tool 调用
  // ------------------------------------------------------------------
  beginToolCall(toolCallId: string, toolName: string, args: Record<string, unknown>): void {
    // Cancel any pending disposal so the indicator stays alive during tool execution.
    this.cancelPendingDisposal();
    const tool = new ToolCallComponent(toolName, args, undefined, false, this.state.ui);
    this.pendingTools.set(toolCallId, tool);
    this.transcript.appendEntry(
      {
        id: this.transcript.nextId(),
        kind: 'tool_call',
        content: `${toolName}(${JSON.stringify(args)})`,
        toolName,
        toolArgs: args,
      },
      tool,
    );
    // Switch the bottom activity indicator to show the current tool call.
    if (!this.activeThinkingActivity) {
      this.activeThinkingActivity = new ThinkingActivityComponent(this.thinkingDraft, this.state.ui);
      this.state.activityContainer.addChild(this.activeThinkingActivity);
    }
    this.activeThinkingActivity.setToolCall(toolName, args);
    this.setPhase('tool');
  }

  endToolCall(toolCallId: string, result: string, isError: boolean): void {
    const tool = this.pendingTools.get(toolCallId);
    if (tool) {
      tool.setResult(result, isError);
      tool.dispose();
      this.pendingTools.delete(toolCallId);
    }
    if (this.pendingTools.size === 0) {
      // No more tools running: switch back to generating if assistant text is
      // still active, otherwise let the normal dispose path handle it.
      if (this.activeThinkingActivity) {
        if (this.activeAssistant) {
          this.activeThinkingActivity.setGenerating();
        } else {
          this.disposeThinkingActivity();
        }
      }
      this.setPhase('waiting');
    } else {
      // Show the next pending tool in the activity indicator.
      const nextTool = this.pendingTools.values().next().value as ToolCallComponent | undefined;
      if (nextTool && this.activeThinkingActivity) {
        this.activeThinkingActivity.setToolCall(nextTool.toolName, nextTool.toolArgs ?? {});
      }
    }
  }

  finalizePendingTools(reason: string, isError = false): void {
    for (const [id, tool] of this.pendingTools) {
      tool.setResult(reason, isError);
      tool.dispose();
      this.pendingTools.delete(id);
    }
  }

  toggleExpanded(): void {
    // Find the most recent expandable component (thinking or finished tool) and toggle it.
    const children = this.state.transcriptContainer.children;
    for (let i = children.length - 1; i >= 0; i--) {
      const child = children[i];
      if (child instanceof ToolCallComponent && child.phaseValue !== 'pending') {
        child.setExpanded(!child.expanded);
        this.state.ui.requestRender();
        return;
      }
      if (child instanceof ThinkingComponent) {
        child.toggleExpanded();
        this.state.ui.requestRender();
        return;
      }
    }
  }

  // ------------------------------------------------------------------
  // Phase 管理
  // ------------------------------------------------------------------
  setPhase(phase: 'idle' | 'waiting' | 'thinking' | 'composing' | 'tool'): void {
    this.state.appState.streamingPhase = phase === 'tool' ? 'waiting' : phase;
    this.state.ui.requestRender();
  }

  reset(): void {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = undefined;
    }
    this.assistantDraft = '';
    this.thinkingDraft = '';
    this.state.appState.thinkingText = '';
    this.state.appState.thinking = false;
    this.pendingAssistantFlush = false;
    this.pendingThinkingFlush = false;
    this.finalizePendingTools('completed', false);
    this.disposeThinkingActivity(true);
    this.activeAssistant = undefined;
    this.setPhase('idle');
  }

  get isStreaming(): boolean {
    return this.state.appState.streamingPhase !== 'idle';
  }
}
