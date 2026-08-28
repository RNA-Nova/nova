/**
 * skill 调用条目（复刻 pi skill-invocation-message.ts）。
 *
 * ``/skill:name`` 经后端展开为 XML skill block 注入 user 消息
 * （``<skill name="..." location="...">...</skill>`` + 可选尾部用户文本，
 * 格式与 pi 完全同构）——本组件把 skill block 部分从用户文本拆出，
 * 折叠为 ``[skill] name`` 单行（ctrl+o 展开看全文），避免 XML 全文
 * 刷屏（用户视角的噪声）。
 */

import { Box, Markdown, Spacer, Text } from '@earendil-works/pi-tui';

import { colors, markdownTheme } from '../../themes/index.js';
import { keyText } from '../pickers/hints.js';
import type { ExpansionState } from './expansion.js';

/** 解析出的 skill block（pi ParsedSkillBlock 对位）。 */
export interface ParsedSkillBlock {
  name: string;
  location: string;
  content: string;
  /** skill block 之后的用户文本（\n\n 分隔；无则 undefined）。 */
  userMessage?: string;
}

/**
 * 从 user 消息文本解析 skill block（pi 正则直搬——后端 expand_skill_command
 * 的产物格式与之同构）。整消息须以 skill block 开头；不匹配返回 null。
 */
export function parseSkillBlock(text: string): ParsedSkillBlock | null {
  const match = text.match(
    /^<skill name="([^"]+)" location="([^"]+)">\n([\s\S]*?)\n<\/skill>(?:\n\n([\s\S]+))?$/,
  );
  if (!match) return null;
  return {
    name: match[1] ?? '',
    location: match[2] ?? '',
    content: match[3] ?? '',
    userMessage: match[4],
  };
}

/** skill 调用条目视图（折叠/展开两态跟随全局 expansion）。 */
export class SkillInvocationView extends Box {
  constructor(
    private readonly skillBlock: ParsedSkillBlock,
    private readonly expansion: ExpansionState,
  ) {
    super(1, 1, (content: string) => colors.customMessageBg(content));
    this.refresh();
  }

  /** 重建内容（ctrl+o 全局展开态切换后由 transcript rebuildAll 重建，此处供构造与防御刷新）。 */
  refresh(): void {
    this.clear();
    const label = colors.customMessageLabel('[skill]');
    if (this.expansion.expanded) {
      this.addChild(new Text(label, 0, 0));
      const header = `**${this.skillBlock.name}**\n\n`;
      this.addChild(
        new Markdown(header + this.skillBlock.content, 0, 0, markdownTheme, {
          color: colors.customMessageText,
        }),
      );
    } else {
      this.addChild(
        new Text(
          `${label} ${colors.customMessageText(this.skillBlock.name)}` +
            colors.dim(` (${keyText('app.tools.expand')} to expand)`),
          0,
          0,
        ),
      );
    }
    this.addChild(new Spacer(1));
  }
}
