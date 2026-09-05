/**
 * TranscriptController：store.entries → chatContainer 组件树。
 *
 * reconcile 策略：每条 entry 一个组件，Map<entryId, Component> 关联；
 * 新增追加尾部（条目流 append-only）；streaming/running 整体重建；
 * 消失的 id（新会话/压缩重建）整组件移除。
 *
 * custom 条目按 customType 路由到专用视图（bashExecution/compaction/
 * branch_summary/custom 消息），展开状态共享（ctrl+o 全局切换）。
 */

import type { NovaUIRuntime, TranscriptEntry } from 'nova-tui';
import { entrySlot, guardComponentLineWidth, type EntryRenderer, type NovaBlock } from 'nova-tui';
import { Container, Markdown, Text, type Component } from '@earendil-works/pi-tui';

import { blocksToComponents } from '../blocks/index.js';
import { colors, markdownTheme } from '../themes/index.js';
import { AssistantView } from '../components/transcript/assistant-message.js';
import { BranchSummaryView } from '../components/transcript/branch-summary.js';
import { CompactionSummaryView } from '../components/transcript/compaction-summary.js';
import { CustomMessageView } from '../components/transcript/custom-message.js';
import { ToolCardView } from '../components/transcript/tool-execution.js';
import { UserMessageView } from '../components/transcript/user-message.js';

import type { ExpansionState } from '../components/transcript/expansion.js';
export type { ExpansionState } from '../components/transcript/expansion.js';

/** 活组件判型（entry 渲染器双形态之一——带 update 的 pi-tui 组件）。 */
function hasUpdate(component: Component): component is Component & {
  update: (data: unknown) => void;
} {
  return (
    typeof (component as { update?: unknown }).update === 'function'
  );
}

export class TranscriptController {
  private readonly components = new Map<string, Component>();

  constructor(
    private readonly tui: { requestRender: () => void },
    private readonly chatContainer: Container,
    private readonly runtime: NovaUIRuntime,
    private readonly expansion: ExpansionState,
    /** thinking 显隐判定（装配根注入——settings.hide_thinking_block 现取）。 */
    private readonly hideThinking: () => boolean = () => false,
  ) {}

  /** store transcript 变更 → reconcile 组件树。 */
  onChange(): void {
    const entries = this.runtime.store.entries;
    const seen = new Set<string>();

    for (const entry of entries) {
      seen.add(entry.id);
      const existing = this.components.get(entry.id);
      if (existing) {
        this.updateEntry(entry, existing);
      } else {
        const component = this.createEntry(entry);
        this.components.set(entry.id, component);
        this.chatContainer.addChild(component);
      }
    }

    for (const [id, component] of [...this.components]) {
      if (!seen.has(id)) {
        // 有生命周期的组件先 dispose 再移除（幂等可选调用）
        (component as { dispose?: () => void }).dispose?.();
        this.chatContainer.removeChild(component);
        this.components.delete(id);
      }
    }

    this.tui.requestRender();
  }

  /** 本地临时消息（不进入会话——命令反馈/错误提示直接进转录区尾部）。 */
  addError(error: unknown): void {
    const message = error instanceof Error ? error.message : String(error);
    this.chatContainer.addChild(new Text(colors.error(`error: ${message}`), 1, 0));
    this.tui.requestRender();
  }

  addInfo(message: string): void {
    this.chatContainer.addChild(new Text(colors.dim(message), 1, 0));
    this.tui.requestRender();
  }

  /** 本地 Markdown 块（/changelog 等富文本命令反馈——与会话无关的临时呈现）。 */
  addMarkdown(markdown: string): void {
    this.chatContainer.addChild(new Markdown(markdown, 1, 0, markdownTheme));
    this.tui.requestRender();
  }

  /** 复制最后一条 assistant 消息到剪贴板（/copy、ctrl+x 共用）。 */
  async copyLastAssistantMessage(): Promise<void> {
    const entries = this.runtime.store.entries;
    const last = [...entries].reverse().find((entry) => entry.kind === 'assistant');
    if (!last || last.kind !== 'assistant' || !last.text.trim()) {
      this.addInfo('没有可复制的 assistant 消息');
      return;
    }
    const { writeClipboardText } = await import('../utils/clipboard.js');
    const ok = await writeClipboardText(last.text);
    this.addInfo(ok ? '已复制最后一条回复' : '剪贴板写入失败（平台不支持或无权限）');
  }

  /** 展开状态切换后全量重建（折叠类视图需要按新状态重画）。 */
  rebuildAll(): void {
    for (const component of this.components.values()) {
      (component as { dispose?: () => void }).dispose?.();
    }
    this.components.clear();
    this.chatContainer.clear();
    this.onChange();
  }

  private createEntry(entry: TranscriptEntry): Component {
    switch (entry.kind) {
      case 'user':
        return new UserMessageView(entry.text, this.expansion);
      case 'assistant':
        return new AssistantView(
          entry.text,
          entry.thinking,
          entry.stopReason,
          entry.errorMessage,
          this.hideThinking,
          entry.thinkingDurationMs,
          entry.toolCounts,
        );
      case 'toolCall':
        return new ToolCardView(this.runtime, entry.card, this.expansion, () =>
          this.tui.requestRender(),
        );
      case 'notice': {
        const color = entry.level === 'error' ? colors.error : colors.dim;
        return new Text(color(entry.text), 1, 0);
      }
      case 'custom':
        return this.createCustomEntry(entry);
    }
  }

  private createCustomEntry(entry: Extract<TranscriptEntry, { kind: 'custom' }>): Component {
    switch (entry.customType) {
      case 'compaction':
        return new CompactionSummaryView(entry.data, this.expansion.expanded);
      case 'branch_summary':
        return new BranchSummaryView(entry.data, this.expansion.expanded);
      default: {
        // 扩展注册的条目渲染器（entry:<customType> slot——双形态：
        // NovaBlock[] 静态块 / 活组件（带可选 update——updateEntry 回调重绘））
        const renderer = this.runtime.slots.resolve<
          { customType: string; data: unknown },
          ReturnType<EntryRenderer>
        >(entrySlot(entry.customType));
        if (renderer !== undefined) {
          // 包侧渲染器全链隔离：异常/畸形产物降级为默认视图——第三方
          // 渲染器不得带走整个 TUI（离流式数据最近的高频路径）；产物
          // 统一过行宽防线
          let rendered: unknown;
          try {
            rendered = renderer({ customType: entry.customType, data: entry.data });
          } catch {
            rendered = undefined;
          }
          if (Array.isArray(rendered)) {
            if (rendered.length > 0) {
              const container = new Container();
              for (const component of blocksToComponents(rendered, this.runtime.slots)) {
                container.addChild(guardComponentLineWidth(component));
              }
              return container;
            }
          } else if (
            typeof rendered === 'object' &&
            rendered !== null &&
            typeof (rendered as { render?: unknown }).render === 'function'
          ) {
            return guardComponentLineWidth(rendered as Component);
          }
        }
        return new CustomMessageView(entry.customType, entry.data);
      }
    }
  }

  private updateEntry(entry: TranscriptEntry, component: Component): void {
    if (entry.kind === 'assistant' && component instanceof AssistantView) {
      component.update(
        entry.text,
        entry.thinking,
        entry.stopReason,
        entry.errorMessage,
        entry.thinkingDurationMs,
        entry.toolCounts,
      );
    } else if (entry.kind === 'toolCall' && component instanceof ToolCardView) {
      component.update(entry.card);
    } else if (entry.kind === 'custom' && hasUpdate(component)) {
      // 活组件条目（entry:<customType> 槽注册——如 bashExecution：流式创建、
      // chunk 累积、message_end 定稿）数据变化时回调重绘；
      // 没有它命令串会停在初始空值（实证过的缺陷）
      try {
        component.update(entry.data);
      } catch {
        // 包组件 update 异常不得中断归约——下帧重建兜底
      }
    }
    // user/notice/其余 custom 创建后不变
  }
}
