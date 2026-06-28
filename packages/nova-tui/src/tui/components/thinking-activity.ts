/**
 * 底部动态活动指示器。
 *
 * 显示在 editor 上方（activityContainer），不占用 transcript 消息流。
 * 支持两种模式：
 *   - thinking：展示 thinking 内容的最后几行（自动追底）+ 思考时间/token 估算
 *   - tool-call：展示当前正在执行的工具调用（工具名 + 关键参数）
 *
 * 在 thinking 和 tool-call 之间自动切换，让用户清楚知道当前 Agent 正在做什么。
 */

import type { Component } from '@earendil-works/pi-tui';
import { wrapTextWithAnsi } from '@earendil-works/pi-tui';
import chalk from 'chalk';

import { getColors } from '../theme/colors.js';

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const TOOL_SPINNER_FRAMES = ['◐', '◓', '◑', '◒'];
const SPINNER_INTERVAL_MS = 150;
const CONTENT_LINES = 3;
const INDENT = '  ';

export interface TokenStats {
  input: number;
  output: number;
  cacheRead?: number;
  cacheWrite?: number;
  totalTokens?: number;
}

export class ThinkingActivityComponent implements Component {
  private mode: 'waiting' | 'thinking' | 'tool-call' | 'assistant-text' = 'thinking';
  private text = '';
  private spinnerFrame = 0;
  private spinnerInterval: ReturnType<typeof setInterval> | undefined;
  private ui?: import('@earendil-works/pi-tui').TUI;

  // Tool-call mode state
  private toolName = '';
  private toolArgs: Record<string, unknown> = {};

  // Timing — each mode has its own timer
  private readonly startTime: number;
  private modeStartTime: number;
  private elapsedMs = 0;
  private timerRunning = true;

  // Token stats (set when thinking ends)
  private tokenStats: TokenStats | undefined;

  constructor(text: string, ui?: import('@earendil-works/pi-tui').TUI) {
    this.text = text;
    this.ui = ui;
    this.startTime = Date.now();
    this.modeStartTime = this.startTime;
    this.startSpinner();
  }

  setText(text: string): void {
    this.text = text;
  }

  setTokenStats(stats: TokenStats): void {
    this.tokenStats = stats;
  }

  setWaiting(): void {
    this.mode = 'waiting';
    this.modeStartTime = Date.now();
    if (!this.timerRunning) {
      this.timerRunning = true;
      this.startSpinner();
    }
  }

  setThinking(): void {
    if (this.mode !== 'thinking') {
      this.mode = 'thinking';
      this.modeStartTime = Date.now();
      if (!this.timerRunning) {
        this.timerRunning = true;
        this.startSpinner();
      }
    }
  }

  setToolCall(toolName: string, args: Record<string, unknown>): void {
    this.mode = 'tool-call';
    this.toolName = toolName;
    this.toolArgs = args;
    this.modeStartTime = Date.now();
    if (!this.timerRunning) {
      this.timerRunning = true;
      this.startSpinner();
    }
  }

  setGenerating(): void {
    if (this.mode !== 'assistant-text') {
      this.mode = 'assistant-text';
      this.modeStartTime = Date.now();
      if (!this.timerRunning) {
        this.timerRunning = true;
        this.startSpinner();
      }
    }
  }

  clearToolCall(): void {
    if (this.mode === 'tool-call') {
      this.mode = 'thinking';
      this.modeStartTime = Date.now();
      this.toolName = '';
      this.toolArgs = {};
    }
  }

  stopTimer(): void {
    this.timerRunning = false;
    if (this.spinnerInterval) {
      clearInterval(this.spinnerInterval);
      this.spinnerInterval = undefined;
    }
  }

  dispose(): void {
    this.stopTimer();
  }

  invalidate(): void {}

  private formatDuration(ms: number): string {
    if (ms < 1000) return '<1s';
    return `${(ms / 1000).toFixed(1)}s`;
  }

  private formatTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
  }

  private extractKeyArgument(): string | null {
    const keyMap: Record<string, string[]> = {
      bash: ['command'],
      read: ['path', 'file_path'],
      write: ['path', 'file_path'],
      edit: ['path', 'file_path'],
    };
    const candidates = keyMap[this.toolName] ?? Object.keys(this.toolArgs);
    for (const key of candidates) {
      const val = this.toolArgs[key];
      if (typeof val === 'string' && val.length > 0) {
        const firstLine = val.split('\n')[0] ?? val;
        if (firstLine.length > 60) {
          if (key === 'command' || key === 'path' || key === 'file_path') {
            return '…' + firstLine.slice(firstLine.length - 59);
          }
          return firstLine.slice(0, 57) + '...';
        }
        return firstLine;
      }
    }
    return null;
  }

  private buildHeader(): string {
    const colors = getColors();
    const elapsed = this.timerRunning ? Date.now() - this.modeStartTime : this.elapsedMs;
    const dur = this.formatDuration(elapsed);

    if (this.mode === 'waiting') {
      const spinner = SPINNER_FRAMES[this.spinnerFrame] ?? SPINNER_FRAMES[0];
      const header = `${spinner} waiting · ${dur}`;
      return chalk.hex(colors.primary).bold(header);
    }

      if (this.mode === 'tool-call') {
      const spinner = SPINNER_FRAMES[this.spinnerFrame] ?? SPINNER_FRAMES[0];
      const header = `${spinner} executing · ${dur}`;
      return chalk.hex(colors.primary).bold(header);
    }

    if (this.mode === 'assistant-text') {
      const spinner = SPINNER_FRAMES[this.spinnerFrame] ?? SPINNER_FRAMES[0];
      const header = `${spinner} generating · ${dur}`;
      return chalk.hex(colors.primary).bold(header);
    }

    const icon = this.tokenStats
      ? '●'
      : SPINNER_FRAMES[this.spinnerFrame] ?? SPINNER_FRAMES[0];
    const label = this.tokenStats ? 'thought' : 'thinking';
    let header = `${icon} ${label} · ${dur}`;

    if (this.tokenStats) {
      const { input, output, cacheRead, cacheWrite } = this.tokenStats;
      const parts: string[] = [];
      parts.push(`${this.formatTokens(input)}→${this.formatTokens(output)}`);
      if (cacheRead) parts.push(`cr ${this.formatTokens(cacheRead)}`);
      if (cacheWrite) parts.push(`cw ${this.formatTokens(cacheWrite)}`);
      header += ` · ${parts.join(' ')}`;
    } else {
      const estimated = Math.ceil(this.text.length / 3);
      if (estimated > 0) {
        header += ` · ~${this.formatTokens(estimated)}`;
      }
    }

    return chalk.hex(colors.roleThinking).bold(header);
  }

  render(width: number): string[] {
    const colors = getColors();

    const headerRaw = this.buildHeader();
    const header = this.padOrTruncate(headerRaw, width);

    const rendered: string[] = [header];
    const emptyLine = ' '.repeat(width);

    if (this.mode === 'waiting') {
      // Waiting mode: show a simple animated ellipsis in the content area.
      const dots = ['', '.', '..', '...'];
      const dotLine = chalk.hex(colors.textDim).italic(
        INDENT + '▏ ' + dots[this.spinnerFrame % dots.length]
      );
      rendered.push(this.padOrTruncate(dotLine, width));
      for (let i = 1; i < CONTENT_LINES; i++) {
        rendered.push(emptyLine);
      }
      return rendered;
    }

    if (this.mode === 'tool-call') {
      // Tool-call mode: keep the activity bar simple, just the header + blank lines.
      // The persistent tool card is already in the transcript.
      for (let i = 0; i < CONTENT_LINES; i++) {
        rendered.push(emptyLine);
      }
      return rendered;
    }

    if (this.mode === 'assistant-text') {
      // Assistant-text mode: content is already in the transcript, keep bar minimal.
      for (let i = 0; i < CONTENT_LINES; i++) {
        rendered.push(emptyLine);
      }
      return rendered;
    }

    const prefixWidth = INDENT.length + 2; // "  ▏ "
    const contentWidth = Math.max(1, width - prefixWidth);
    const wrapped = wrapTextWithAnsi(this.text, contentWidth);
    const tailLines = wrapped.slice(-CONTENT_LINES);

    const padLines = Math.max(0, CONTENT_LINES - tailLines.length);
    for (let i = 0; i < padLines; i++) {
      rendered.push(emptyLine);
    }

    const bar = chalk.hex(colors.roleThinking)('▏');
    for (let i = 0; i < tailLines.length; i++) {
      const line = tailLines[i];
      const age = tailLines.length - 1 - i;
      let styled: string;
      if (age === 0) {
        styled = chalk.hex(colors.roleThinking).italic.bold(line);
      } else if (age === 1) {
        styled = chalk.hex(colors.textDim).italic(line);
      } else {
        styled = chalk.hex(colors.textMuted).italic(line);
      }
      const indented = `${INDENT}${bar} ${styled}`;
      rendered.push(this.padOrTruncate(indented, width));
    }

    return rendered;
  }

  private padOrTruncate(raw: string, width: number): string {
    const visibleLen = this.visibleWidth(raw);
    if (visibleLen > width) {
      return this.truncateToWidth(raw, width, '…');
    }
    if (visibleLen < width) {
      return raw + ' '.repeat(width - visibleLen);
    }
    return raw;
  }

  private visibleWidth(str: string): number {
    const stripped = str.replace(/\u001b\[[0-9;]*m/g, '');
    let width = 0;
    for (const ch of stripped) {
      const code = ch.codePointAt(0) ?? 0;
      width += code >= 0x1100 && this.isWide(code) ? 2 : 1;
    }
    return width;
  }

  private isWide(code: number): boolean {
    return (
      (code >= 0x1100 && code <= 0x115f) ||
      (code >= 0x2e80 && code <= 0x9fff) ||
      (code >= 0xac00 && code <= 0xd7af) ||
      (code >= 0xf900 && code <= 0xfaff) ||
      (code >= 0xfe30 && code <= 0xfe6f) ||
      (code >= 0xff00 && code <= 0xff60) ||
      (code >= 0xffe0 && code <= 0xffe6) ||
      (code >= 0x20000 && code <= 0x2fffd) ||
      (code >= 0x30000 && code <= 0x3fffd)
    );
  }

  private truncateToWidth(str: string, maxWidth: number, ellipsis = '…'): string {
    let result = '';
    let width = 0;
    const stripped = str.replace(/\u001b\[[0-9;]*m/g, '');
    let ansiOffset = 0;
    for (const ch of stripped) {
      const code = ch.codePointAt(0) ?? 0;
      const w = code >= 0x1100 && this.isWide(code) ? 2 : 1;
      if (width + w + this.visibleWidth(ellipsis) > maxWidth) {
        result += ellipsis;
        break;
      }
      const idx = str.indexOf(ch, ansiOffset);
      if (idx > ansiOffset) {
        result += str.slice(ansiOffset, idx);
      }
      result += ch;
      ansiOffset = idx + 1;
      width += w;
    }
    return result;
  }

  private startSpinner(): void {
    if (this.spinnerInterval) return;
    this.spinnerInterval = setInterval(() => {
      this.spinnerFrame = (this.spinnerFrame + 1) % SPINNER_FRAMES.length;
      if (this.timerRunning) {
        this.elapsedMs = Date.now() - this.startTime;
      }
      this.ui?.requestRender();
    }, SPINNER_INTERVAL_MS);
  }
}
