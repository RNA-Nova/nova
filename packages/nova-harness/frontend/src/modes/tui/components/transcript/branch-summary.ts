/**
 * 分支摘要卡片（复刻 pi branch-summary-message.ts，与压缩摘要同构）。
 */

import { Box, Markdown, Spacer, Text } from '@earendil-works/pi-tui';

import { colors, markdownTheme } from '../../themes/index.js';

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}

export class BranchSummaryView extends Box {
  constructor(data: unknown, expanded: boolean) {
    super(1, 1, (t) => colors.customMessageBg(t));
    const record = asRecord(data);
    const summary = typeof record.summary === 'string' ? record.summary : '';

    this.addChild(new Text(colors.customMessageLabel('[branch summary]'), 0, 0));
    this.addChild(new Spacer(1));

    if (expanded) {
      this.addChild(
        new Markdown(summary, 0, 0, markdownTheme, {
          color: colors.customMessageText,
        }),
      );
    } else {
      this.addChild(
        new Text(
          colors.customMessageText('Branch summary') + colors.dim(' (ctrl+o to expand)'),
          0,
          0,
        ),
      );
    }
  }
}
