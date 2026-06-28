/**
 * 助手消息组件（Markdown 渲染）。
 *
 * 支持把 Markdown 代码块后处理成 box-drawing 四边框。
 */

import type { Component } from '@earendil-works/pi-tui';
import { Markdown, visibleWidth, truncateToWidth } from '@earendil-works/pi-tui';
import chalk from 'chalk';

import { getColors } from '../theme/colors.js';
import {
  createMarkdownTheme,
  type MarkdownThemeInternal,
  CB_BOT_MARKER,
  CB_LINE_PREFIX,
} from '../theme/pi-tui-theme.js';

const BOX_H = '─';
const BOX_V = '│';
const BOX_TL = '┌';
const BOX_TR = '┐';
const BOX_BL = '└';
const BOX_BR = '┘';

const CODE_BG = '#2c313a';

export class AssistantMessageComponent implements Component {
  private markdown: Markdown;
  private theme: MarkdownThemeInternal;
  private text = '';

  constructor(initialText: string = '') {
    this.theme = createMarkdownTheme(getColors());
    this.markdown = new Markdown(initialText, 0, 0, this.theme);
  }

  updateContent(text: string): void {
    this.text = text;
    this.theme._fenceCount = 0;
    this.markdown.setText(text);
    this.markdown.invalidate();
  }

  appendContent(text: string): void {
    this.text += text;
    this.theme._fenceCount = 0;
    this.markdown.setText(this.text);
    this.markdown.invalidate();
  }

  invalidate(): void {
    this.markdown.invalidate();
  }

  render(width: number): string[] {
    const colors = getColors();
    const bullet = chalk.hex(colors.roleAssistant).bold('● ');
    const bulletWidth = visibleWidth(bullet);
    const contentWidth = Math.max(1, width - bulletWidth);
    const childLines = this.markdown.render(contentWidth);
    if (childLines.length === 0) return [];

    const boxedLines = this.boxCodeBlocks(childLines, contentWidth, colors);

    const lines: string[] = [];
    for (let i = 0; i < boxedLines.length; i++) {
      const prefix = i === 0 ? bullet : ' '.repeat(bulletWidth);
      lines.push(prefix + boxedLines[i]);
    }
    return lines;
  }

  private boxCodeBlocks(
    lines: string[],
    width: number,
    colors: ReturnType<typeof getColors>,
  ): string[] {
    const result: string[] = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const topMatch = line.match(new RegExp(`\\u0007CBTOP:([^\\u0007]*)\\u0007`));
      if (topMatch) {
        const lang = topMatch[1] ?? '';
        i++;
        const codeRows: string[] = [];
        let closed = false;
        while (i < lines.length) {
          const inner = lines[i];
          if (inner.trim() === CB_BOT_MARKER) {
            closed = true;
            i++;
            break;
          }
          const cbIdx = inner.indexOf(CB_LINE_PREFIX);
          if (cbIdx !== -1) {
            codeRows.push(inner.slice(0, cbIdx) + inner.slice(cbIdx + CB_LINE_PREFIX.length));
          } else {
            codeRows.push(inner);
          }
          i++;
        }
        if (!closed) {
          result.push(line);
          result.push(...codeRows);
          continue;
        }
        result.push(...this.renderBox(width, lang, codeRows, colors));
      } else {
        result.push(line);
        i++;
      }
    }
    return result;
  }

  private renderBox(
    width: number,
    lang: string,
    codeRows: string[],
    colors: ReturnType<typeof getColors>,
  ): string[] {
    const borderColor = colors.border;
    const textStrong = colors.textStrong;
    const success = colors.success;

    const margin = 1; // one space on each side inside the box
    const innerWidth = Math.max(1, width - 2 - margin * 2);

    const topLabel = lang ? ` ${lang} ` : '';
    const topLabelWidth = visibleWidth(topLabel);
    const sideFill = Math.max(0, innerWidth + margin * 2 - topLabelWidth);
    const leftFill = Math.floor(sideFill / 2);
    const rightFill = sideFill - leftFill;

    const topBorder =
      chalk.hex(borderColor)(BOX_TL) +
      chalk.hex(borderColor)(BOX_H.repeat(leftFill)) +
      chalk.hex(textStrong).bold(topLabel) +
      chalk.hex(borderColor)(BOX_H.repeat(rightFill)) +
      chalk.hex(borderColor)(BOX_TR);

    const bottomBorder =
      chalk.hex(borderColor)(BOX_BL) +
      chalk.hex(borderColor)(BOX_H.repeat(innerWidth + margin * 2)) +
      chalk.hex(borderColor)(BOX_BR);

    const rendered: string[] = [this.padToWidth(topBorder, width)];

    for (const row of codeRows) {
      let trimmed = row;
      if (visibleWidth(row) > innerWidth) {
        trimmed = truncateToWidth(row, innerWidth, '', false);
      }
      const visible = visibleWidth(trimmed);
      const pad = Math.max(0, innerWidth - visible);
      const body = trimmed + ' '.repeat(pad);
      const line =
        chalk.hex(borderColor)(BOX_V) +
        ' ' +
        chalk.bgHex(CODE_BG).hex(success)(body) +
        ' ' +
        chalk.hex(borderColor)(BOX_V);
      rendered.push(this.padToWidth(line, width));
    }

    rendered.push(this.padToWidth(bottomBorder, width));
    return rendered;
  }

  private padToWidth(line: string, width: number): string {
    const v = visibleWidth(line);
    if (v < width) {
      return line + ' '.repeat(width - v);
    }
    return line;
  }
}
