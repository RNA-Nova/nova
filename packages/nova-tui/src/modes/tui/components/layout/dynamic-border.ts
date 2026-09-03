/** 动态边框：随视口宽度伸缩的水平线（复刻 pi dynamic-border.ts）。 */

import type { Component } from '@earendil-works/pi-tui';

import { colors } from '../../themes/index.js';

export class DynamicBorder implements Component {
  constructor(
    private readonly color: (str: string) => string = colors.borderMuted,
  ) {}

  invalidate(): void {}

  render(width: number): string[] {
    return [this.color('─'.repeat(Math.max(1, width)))];
  }
}
