/**
 * 扩展 custom 消息视图（复刻 pi custom-message.ts）。
 *
 * Spacer + Box（customMessageBg）+ [customType] 标签 + Markdown 正文。
 * 定制渲染器（entry:<customType> slot）归 M4，v1 走默认渲染。
 */

import { Box, Container, Markdown, Spacer, Text } from '@earendil-works/pi-tui';

import { colors, markdownTheme } from '../../themes/index.js';

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}

function extractText(data: unknown): string {
  const record = asRecord(data);
  // CustomItem 形态：内容在 details（command_result 等 custom 条目为
  // details.text；custom 消息为 details.content）；兼容直接携带字段的形态
  const details = asRecord(record.details);
  const content = record.content ?? details.content;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === 'object' && block !== null && 'text' in block) {
          const text = (block as { text?: unknown }).text;
          if (typeof text === 'string') return text;
        }
        return '';
      })
      .filter(Boolean)
      .join('\n');
  }
  if (typeof record.text === 'string') return record.text;
  if (typeof details.text === 'string') return details.text;
  return '';
}

export class CustomMessageView extends Container {
  constructor(customType: string, data: unknown) {
    super();
    this.addChild(new Spacer(1));

    const box = new Box(1, 1, (t) => colors.customMessageBg(t));
    box.addChild(new Text(colors.customMessageLabel(`[${customType}]`), 0, 0));
    box.addChild(new Spacer(1));

    const text = extractText(data);
    if (text) {
      box.addChild(
        new Markdown(text, 0, 0, markdownTheme, { color: colors.customMessageText }),
      );
    }
    this.addChild(box);
  }
}
