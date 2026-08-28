/**
 * 用户消息视图（复刻 pi components/user-message.ts）。
 *
 * Box 背景（userMessageBg）+ Markdown（保留列表标记与反斜杠转义），
 * 首末行注入 OSC 133 区段标记。
 *
 * skill block 拆分：消息以 ``<skill ...>...</skill>`` 开头时（/skill:name
 * 展开产物），skill 部分渲染为折叠的 [skill] 条目（ctrl+o 展开全文），
 * 尾部用户文本仍走正常消息呈现（pi 同款拆分语义）。
 */

import { Box, Container, Markdown, Spacer } from '@earendil-works/pi-tui';

import { colors, markdownTheme } from '../../themes/index.js';
import type { ExpansionState } from './expansion.js';
import { SkillInvocationView, parseSkillBlock } from './skill-invocation.js';

const OSC133_ZONE_START = '\x1b]133;A\x07';
const OSC133_ZONE_END = '\x1b]133;B\x07';
const OSC133_ZONE_FINAL = '\x1b]133;C\x07';

export class UserMessageView extends Container {
  constructor(text: string, expansion: ExpansionState) {
    super();
    const skillBlock = parseSkillBlock(text);
    if (skillBlock !== null) {
      this.addChild(new SkillInvocationView(skillBlock, expansion));
      const remainder = skillBlock.userMessage?.trim();
      if (!remainder) return; // 纯 skill 调用：无尾部文本
      this.addUserText(remainder);
      return;
    }
    this.addUserText(text);
  }

  /** 正常用户文本区（Box 背景 + Markdown）。 */
  private addUserText(text: string): void {
    const contentBox = new Box(1, 1, (content: string) => colors.userMessageBg(content));
    contentBox.addChild(
      new Markdown(text, 0, 0, markdownTheme, { color: colors.userMessageText }, {
        preserveOrderedListMarkers: true,
        preserveBackslashEscapes: true,
      }),
    );
    this.addChild(contentBox);
    this.addChild(new Spacer(1));
  }

  override render(width: number): string[] {
    const lines = super.render(width);
    if (lines.length === 0) return lines;
    lines[0] = OSC133_ZONE_START + lines[0];
    lines[lines.length - 1] = OSC133_ZONE_END + OSC133_ZONE_FINAL + lines[lines.length - 1];
    return lines;
  }
}
