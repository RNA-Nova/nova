/**
 * 工具调用展示组件。
 *
 * 设计参考 kimi-code：
 *   - 工具调用是 transcript 的一部分，但保持极低视觉噪音
 *   - pending 状态使用旋转圆点 spinner，表示进行中
 *   - 工具名用蓝色粗体突出，动词/参数用灰色弱化
 *   - 默认只显示一行 header；按 Ctrl+O 展开查看完整 args 和 result
 */

import type { Component, TUI } from '@earendil-works/pi-tui';
import { Text } from '@earendil-works/pi-tui';
import chalk from 'chalk';

import { getColors } from '../theme/colors.js';

const MAX_ARG_LENGTH = 60;
const PATH_KEYS = new Set(['path', 'file_path']);
const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const SPINNER_INTERVAL_MS = 80;

function truncateArgValue(key: string, value: string): string {
  if (value.length <= MAX_ARG_LENGTH) return value;
  if (PATH_KEYS.has(key)) {
    return '…' + value.slice(value.length - (MAX_ARG_LENGTH - 1));
  }
  return value.slice(0, MAX_ARG_LENGTH - 3) + '...';
}

function extractKeyArgument(
  toolName: string,
  args: Record<string, unknown>,
): string | null {
  const keyMap: Record<string, string[]> = {
    bash: ['command'],
    read: ['path', 'file_path'],
    write: ['path', 'file_path'],
    edit: ['path', 'file_path'],
  };

  const candidates = keyMap[toolName] ?? Object.keys(args);
  for (const key of candidates) {
    const val = args[key];
    if (typeof val === 'string' && val.length > 0) {
      const firstLine = val.split('\n')[0] ?? val;
      return truncateArgValue(key, firstLine);
    }
  }
  return null;
}

function formatBody(args: Record<string, unknown>, result?: string, isError = false): string {
  const colors = getColors();
  const lines: string[] = [];
  try {
    lines.push(chalk.hex(colors.textDim)('args: ') + JSON.stringify(args));
  } catch {
    lines.push(chalk.hex(colors.textDim)('args: ') + String(args));
  }
  if (result !== undefined) {
    const color = isError ? colors.error : colors.success;
    lines.push(chalk.hex(color)(`result: ${result}`));
  }
  return lines.join('\n');
}

export class ToolCallComponent implements Component {
  readonly toolName: string;
  private args: Record<string, unknown>;
  private result?: string;
  private isError = false;
  private phase: 'pending' | 'done' | 'error' = 'pending';
  expanded = false;
  private header: Text;
  private body: Text;
  private ui: TUI | undefined;
  private spinnerFrame = 0;
  private spinnerInterval: ReturnType<typeof setInterval> | undefined;

  constructor(
    toolName: string,
    args: Record<string, unknown>,
    result?: string,
    isError = false,
    ui?: TUI,
  ) {
    this.toolName = toolName;
    this.args = args;
    this.result = result;
    this.isError = isError;
    this.phase = result === undefined ? 'pending' : isError ? 'error' : 'done';
    this.ui = ui;
    this.header = new Text(this._buildHeader(), 0, 0);
    this.body = new Text(formatBody(args, result, isError), 0, 0);
    if (this.phase === 'pending') {
      this._startSpinner();
    }
  }

  setResult(result: string, isError: boolean): void {
    this.result = result;
    this.isError = isError;
    this.phase = isError ? 'error' : 'done';
    this._stopSpinner();
    this.body.setText(formatBody(this.args, result, isError));
    this.header.setText(this._buildHeader());
    this.ui?.requestRender();
  }

  setExpanded(value: boolean): void {
    this.expanded = value;
  }

  toggleExpanded(): boolean {
    this.expanded = !this.expanded;
    return true;
  }

  get phaseValue(): 'pending' | 'done' | 'error' {
    return this.phase;
  }

  get toolArgs(): Record<string, unknown> {
    return this.args;
  }

  invalidate(): void {
    this.header.invalidate();
    this.body.invalidate();
  }

  render(width: number): string[] {
    const lines: string[] = [];
    const headerLines = this.header.render(width);
    if (headerLines.length > 0 && headerLines[0] !== undefined) {
      lines.push(headerLines[0]);
    }

    if (this.expanded) {
      for (const line of this.body.render(Math.max(1, width - 2))) {
        if (line !== undefined) {
          lines.push('  ' + line);
        }
      }
    }
    return lines;
  }

  private _buildHeader(): string {
    const colors = getColors();
    const keyArg = extractKeyArgument(this.toolName, this.args);
    const argPart = keyArg ? chalk.hex(colors.textDim)(` (${keyArg})`) : '';
    const toolLabel = chalk.hex(colors.primary).bold(this.toolName);

    if (this.phase === 'pending') {
      const spinner = chalk.hex(colors.roleAssistant)(
        `${SPINNER_FRAMES[this.spinnerFrame] ?? SPINNER_FRAMES[0]} `,
      );
      return `${spinner}${chalk.hex(colors.textDim)('Using')} ${toolLabel}${argPart}`;
    }

    const icon = this.phase === 'error'
      ? chalk.hex(colors.error)('✗ ')
      : chalk.hex(colors.success)('✓ ');
    return `${icon}${chalk.hex(colors.textDim)('Used')} ${toolLabel}${argPart}`;
  }

  private _startSpinner(): void {
    if (!this.ui || this.spinnerInterval) return;
    this.spinnerInterval = setInterval(() => {
      this.spinnerFrame = (this.spinnerFrame + 1) % SPINNER_FRAMES.length;
      this.header.setText(this._buildHeader());
      this.ui?.requestRender();
    }, SPINNER_INTERVAL_MS);
  }

  private _stopSpinner(): void {
    if (!this.spinnerInterval) return;
    clearInterval(this.spinnerInterval);
    this.spinnerInterval = undefined;
  }

  dispose(): void {
    this._stopSpinner();
  }
}
