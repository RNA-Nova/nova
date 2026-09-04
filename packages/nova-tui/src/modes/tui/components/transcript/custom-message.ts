/**
 * 扩展 custom 消息视图。
 *
 * Spacer + Box（customMessageBg）+ [customType] 标签 + Markdown 正文——
 * 这是**默认兜底渲染**；包注册的定制渲染器（`entry:<customType>` slot）
 * 由 transcript 的 entry 路径优先消费（双形态：NovaBlock[] / 活组件）。
 */

import { Box, Container, Markdown, Spacer, Text } from '@earendil-works/pi-tui';

import { colors, markdownTheme } from '../../themes/index.js';

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}

function extractText(data: unknown): string {
  const record = asRecord(data);
  const content = record.content;
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
