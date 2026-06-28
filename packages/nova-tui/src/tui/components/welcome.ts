/**
 * 欢迎界面组件 — 在 transcript 顶部展示 Nova 启动欢迎信息。
 */

import type { Component } from '@earendil-works/pi-tui';
import { visibleWidth } from '@earendil-works/pi-tui';
import chalk from 'chalk';

import { getColors } from '../theme/colors.js';

const BOX_TL = '╭';
const BOX_TR = '╮';
const BOX_BL = '╰';
const BOX_BR = '╯';
const BOX_H = '─';
const BOX_V = '│';

export class WelcomeComponent implements Component {
  constructor(private readonly version: string) {}

  invalidate(): void {}

  render(width: number): string[] {
    const colors = getColors();

    const pad = (line: string, w: number): string => {
      const v = visibleWidth(line);
      if (v < w) return line + ' '.repeat(w - v);
      return line;
    };

    const center = (text: string, w: number, styler: (s: string) => string): string => {
      const v = visibleWidth(text);
      const left = Math.max(0, Math.floor((w - v) / 2));
      const right = Math.max(0, w - v - left);
      return ' '.repeat(left) + styler(text) + ' '.repeat(right);
    };

    // Compute inner width (account for box borders + padding)
    const margin = 2; // one space on each side inside the box
    const innerWidth = Math.max(1, width - 2 - margin * 2);

    const borderColor = colors.border;
    const primary = colors.primary;
    const textStrong = colors.textStrong;
    const textDim = colors.textDim;

    // Title line: "N O V A"
    const title = 'N O V A';
    const titleLine = center(title, innerWidth, (s) => chalk.hex(primary).bold(s));

    // Version line
    const versionText = `Nova TUI v${this.version}`;
    const versionLine = center(versionText, innerWidth, (s) => chalk.hex(textStrong)(s));

    // Hints
    const hint1 = 'Type a message and press Enter';
    const hint2 = 'Ctrl+C cancel  ·  Ctrl+D exit';
    const hint1Line = center(hint1, innerWidth, (s) => chalk.hex(textDim)(s));
    const hint2Line = center(hint2, innerWidth, (s) => chalk.hex(textDim)(s));

    // Build lines
    const topBorder =
      chalk.hex(borderColor)(BOX_TL) +
      chalk.hex(borderColor)(BOX_H.repeat(innerWidth + margin * 2)) +
      chalk.hex(borderColor)(BOX_TR);

    const bottomBorder =
      chalk.hex(borderColor)(BOX_BL) +
      chalk.hex(borderColor)(BOX_H.repeat(innerWidth + margin * 2)) +
      chalk.hex(borderColor)(BOX_BR);

    const emptyLine =
      chalk.hex(borderColor)(BOX_V) +
      ' '.repeat(innerWidth + margin * 2) +
      chalk.hex(borderColor)(BOX_V);

    const body = (content: string): string =>
      chalk.hex(borderColor)(BOX_V) +
      ' ' +
      pad(content, innerWidth) +
      ' ' +
      chalk.hex(borderColor)(BOX_V);

    const lines: string[] = [
      pad(topBorder, width),
      emptyLine,
      pad(body(titleLine), width),
      pad(body(versionLine), width),
      emptyLine,
      pad(body(hint1Line), width),
      pad(body(hint2Line), width),
      emptyLine,
      pad(bottomBorder, width),
    ];

    return lines;
  }
}
