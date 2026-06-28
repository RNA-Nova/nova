/**
 * 用户消息组件。
 */

import type { Component } from '@earendil-works/pi-tui';
import { Text, visibleWidth } from '@earendil-works/pi-tui';
import chalk from 'chalk';

import { getColors } from '../theme/colors.js';

export class UserMessageComponent implements Component {
  private textComponent: Text;

  constructor(text: string) {
    const colors = getColors();
    this.textComponent = new Text(chalk.hex(colors.roleUser).bold(text), 0, 0);
  }

  invalidate(): void {
    this.textComponent.invalidate();
  }

  render(width: number): string[] {
    const colors = getColors();
    const bullet = chalk.hex(colors.roleUser).bold('◉ ');
    const bulletWidth = visibleWidth(bullet);
    const contentWidth = Math.max(1, width - bulletWidth);

    const textLines = this.textComponent.render(contentWidth);
    const lines: string[] = [];
    for (let i = 0; i < textLines.length; i++) {
      const prefix = i === 0 ? bullet : ' '.repeat(bulletWidth);
      lines.push(prefix + textLines[i]);
    }
    return lines;
  }
}
