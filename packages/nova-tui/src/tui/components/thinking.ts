/**
 * Thinking 内容组件（独立显示，非嵌套在 assistant message 内）。
 *
 * - live 模式：spinner + "thinking..." + 最近 2 行预览
 * - finalized 模式：默认折叠，只显示前 2 行 + "... (N more lines)"
 * - 支持 setText 流式更新，finalize 切换模式
 */

import type { Component, TUI } from '@earendil-works/pi-tui';
import { Text } from '@earendil-works/pi-tui';
import chalk from 'chalk';

import { getColors } from '../theme/colors.js';

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const SPINNER_INTERVAL_MS = 80;
const THINKING_PREVIEW_LINES = 2;
const INDENT = '  ';

export class ThinkingComponent implements Component {
  private text = '';
  private textComponent: Text;
  private mode: 'live' | 'finalized' = 'live';
  private expanded = false;
  private spinnerFrame = 0;
  private spinnerInterval: ReturnType<typeof setInterval> | undefined;
  private ui: TUI | undefined;

  constructor(text: string, ui?: TUI) {
    const colors = getColors();
    this.text = text;
    this.textComponent = new Text(chalk.hex(colors.roleThinking).italic(text), 0, 0);
    this.ui = ui;
    if (this.mode === 'live') {
      this.startSpinner();
    }
  }

  setText(text: string): void {
    if (this.text === text) return;
    this.text = text;
    const colors = getColors();
    this.textComponent.setText(chalk.hex(colors.roleThinking).italic(text));
  }

  finalize(): void {
    this.mode = 'finalized';
    this.stopSpinner();
  }

  setExpanded(expanded: boolean): void {
    this.expanded = expanded;
  }

  toggleExpanded(): void {
    this.expanded = !this.expanded;
  }

  dispose(): void {
    this.stopSpinner();
  }

  invalidate(): void {}

  render(width: number): string[] {
    const colors = getColors();
    const contentWidth = Math.max(1, width - INDENT.length);
    const contentLines =
      this.text.length > 0 ? this.textComponent.render(contentWidth) : [''];

    if (this.mode === 'live') {
      const visibleLines =
        contentLines.length > THINKING_PREVIEW_LINES
          ? contentLines.slice(contentLines.length - THINKING_PREVIEW_LINES)
          : contentLines;
      const spinner = chalk.hex(colors.roleThinking)(
        `${SPINNER_FRAMES[this.spinnerFrame] ?? SPINNER_FRAMES[0]} `,
      );
      return [
        '',
        spinner + chalk.hex(colors.roleThinking).bold('◆'),
        ...visibleLines.map((line) => INDENT + line),
      ];
    }

    const label = chalk.hex(colors.roleThinking).bold('◆');
    const rendered: string[] = ['', label];
    for (let i = 0; i < contentLines.length; i++) {
      rendered.push(INDENT + contentLines[i]);
    }

    if (this.expanded || contentLines.length <= THINKING_PREVIEW_LINES) {
      return rendered;
    }

    const truncated = rendered.slice(0, 2 + THINKING_PREVIEW_LINES);
    const remaining = contentLines.length - THINKING_PREVIEW_LINES;
    truncated.push(
      INDENT + chalk.dim(`... (${String(remaining)} more lines, ctrl+o to expand)`),
    );
    return truncated;
  }

  private startSpinner(): void {
    if (!this.ui || this.spinnerInterval) return;
    this.spinnerInterval = setInterval(() => {
      this.spinnerFrame = (this.spinnerFrame + 1) % SPINNER_FRAMES.length;
      this.ui?.requestRender();
    }, SPINNER_INTERVAL_MS);
  }

  private stopSpinner(): void {
    if (!this.spinnerInterval) return;
    clearInterval(this.spinnerInterval);
    this.spinnerInterval = undefined;
  }
}
