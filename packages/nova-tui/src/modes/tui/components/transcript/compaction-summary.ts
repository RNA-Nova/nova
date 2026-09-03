/**
 * 压缩摘要卡片（复刻 pi compaction-summary-message.ts）。
 *
 * Box（customMessageBg）+ [compaction] 标签 + 折叠态（tokens 数 + 展开提示）
 * / 展开态（完整 Markdown 摘要）。
 */

import { Box, Markdown, Spacer, Text } from '@earendil-works/pi-tui';

import { colors, markdownTheme } from '../../themes/index.js';

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}

export class CompactionSummaryView extends Box {
  constructor(data: unknown, expanded: boolean) {
    super(1, 1, (t) => colors.customMessageBg(t));
    const record = asRecord(data);
    const tokensBefore =
      typeof record.tokensBefore === 'number' ? record.tokensBefore : 0;
    const summary = typeof record.summary === 'string' ? record.summary : '';

    this.addChild(new Text(colors.customMessageLabel('[compaction]'), 0, 0));
    this.addChild(new Spacer(1));

    const tokenStr = tokensBefore.toLocaleString();
    if (expanded) {
      this.addChild(
        new Markdown(`**Compacted from ${tokenStr} tokens**\n\n${summary}`, 0, 0, markdownTheme, {
          color: colors.customMessageText,
        }),
      );
    } else {
      this.addChild(
        new Text(
          colors.customMessageText(`Compacted from ${tokenStr} tokens`) +
            colors.dim(' (ctrl+o to expand)'),
          0,
          0,
        ),
      );
    }
  }
}
