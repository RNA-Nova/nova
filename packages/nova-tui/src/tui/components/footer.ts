/**
 * Footer/status bar — multi-line status display at the bottom of the TUI.
 *
 * Layout:
 *   Line 1: [agent] <model> thinking <cwd>  <shortcut hints>
 *   Line 2: context: XX.X% (tokens/max)
 */

import type { Component } from '@earendil-works/pi-tui';
import { truncateToWidth, visibleWidth } from '@earendil-works/pi-tui';
import chalk from 'chalk';

import type { AppState, StreamingPhase } from '../state.js';
import { getColors } from '../theme/colors.js';

const PHASE_LABEL: Record<StreamingPhase, string> = {
  idle: '',
  waiting: 'waiting',
  thinking: 'thinking',
  composing: 'composing',
};

const TIP_ROTATE_INTERVAL_MS = 10_000;
const TIP_SEPARATOR = ' | ';

interface ToolbarTip {
  readonly text: string;
  readonly solo?: boolean;
  readonly priority?: number;
}

const TOOLBAR_TIPS: readonly ToolbarTip[] = [
  { text: '/agent: switch agent' },
  { text: '/agents: list agents' },
  { text: 'ctrl+c: cancel' },
  { text: 'ctrl+d: exit' },
  { text: 'enter: send' },
];

function buildWeightedTips(tips: readonly ToolbarTip[]): readonly ToolbarTip[] {
  const items = tips.map((t) => ({ tip: t, weight: Math.max(1, Math.trunc(t.priority ?? 1)), current: 0 }));
  const total = items.reduce((sum, it) => sum + it.weight, 0);
  const seq: ToolbarTip[] = [];
  for (let n = 0; n < total; n++) {
    let best = items[0]!;
    for (const it of items) {
      it.current += it.weight;
      if (it.current > best.current) best = it;
    }
    best.current -= total;
    seq.push(best.tip);
  }
  return seq;
}

const ROTATION: readonly ToolbarTip[] = buildWeightedTips(TOOLBAR_TIPS);

function currentTipIndex(): number {
  return Math.floor(Date.now() / TIP_ROTATE_INTERVAL_MS);
}

function tipsForIndex(index: number): { primary: string; pair: string | null } {
  const n = ROTATION.length;
  if (n === 0) return { primary: '', pair: null };
  const offset = ((index % n) + n) % n;
  const current = ROTATION[offset]!;
  if (n === 1 || current.solo) return { primary: current.text, pair: null };
  const next = ROTATION[(offset + 1) % n]!;
  if (next.solo || next.text === current.text) return { primary: current.text, pair: null };
  return { primary: current.text, pair: current.text + TIP_SEPARATOR + next.text };
}

function shortenModel(model: string): string {
  if (!model) return model;
  const slash = model.lastIndexOf('/');
  return slash >= 0 ? model.slice(slash + 1) : model;
}

function shortenCwd(path: string): string {
  if (!path) return path;
  const home = process.env['HOME'] ?? '';
  let work = path;
  if (home && path === home) return '~';
  if (home && path.startsWith(home + '/')) work = '~' + path.slice(home.length);
  const segments = work.split('/').filter((s) => s.length > 0);
  if (segments.length <= 3) return work;
  const tail = segments.slice(-3).join('/');
  return `…/${tail}`;
}

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function contextColor(pct: number): string {
  if (pct >= 90) return '#e06c75';
  if (pct >= 70) return '#e5c07b';
  return '#98c379';
}

function formatContextStatus(usage: number, tokens?: number, maxTokens?: number): string {
  const pct = Math.min(usage * 100, 100).toFixed(1);
  if (maxTokens && maxTokens > 0 && tokens !== undefined) {
    return `context: ${pct}% (${formatTokenCount(tokens)}/${formatTokenCount(maxTokens)})`;
  }
  return `context: ${pct}%`;
}

export class FooterComponent implements Component {
  private state: AppState;
  private transientHint: string | null = null;
  private commandHint: string | null = null;

  constructor(state: AppState) {
    this.state = state;
  }

  setState(state: AppState): void {
    this.state = state;
  }

  setTransientHint(hint: string | null): void {
    this.transientHint = hint;
  }

  setCommandHint(hint: string | null): void {
    this.commandHint = hint;
  }

  invalidate(): void {}

  render(width: number): string[] {
    const colors = getColors();
    const s = this.state;

    // ── Line 1: phase + agent + model + thinking + cwd + hints ──
    const left: string[] = [];

    if (s.streamingPhase !== 'idle') {
      left.push(chalk.hex(colors.warning).bold(`[${PHASE_LABEL[s.streamingPhase]}]`));
    }

    if (s.agentName) {
      left.push(chalk.hex(colors.primary).bold(s.agentName));
    }

    if (s.model) {
      const modelName = shortenModel(s.model);
      const thinkingLabel = s.thinking ? ' thinking' : '';
      left.push(chalk.hex(colors.text)(`${modelName}${thinkingLabel}`));
    }

    const cwd = shortenCwd(s.workDir);
    if (cwd) left.push(chalk.hex(colors.textDim)(cwd));

    const leftLine = left.join('  ');
    const leftWidth = visibleWidth(leftLine);

    // Rotating hint tips
    const { primary, pair } = tipsForIndex(currentTipIndex());
    const gap = 2;
    const remaining = Math.max(0, width - leftWidth - gap);
    let tipText = '';
    if (pair && visibleWidth(pair) <= remaining) {
      tipText = pair;
    } else if (primary && visibleWidth(primary) <= remaining) {
      tipText = primary;
    }

    let line1: string;
    if (tipText) {
      const pad = width - leftWidth - visibleWidth(tipText);
      line1 = leftLine + ' '.repeat(Math.max(0, pad)) + chalk.hex(colors.textMuted)(tipText);
    } else if (leftWidth <= width) {
      line1 = leftLine;
    } else {
      line1 = truncateToWidth(leftLine, width, '…');
    }

    // ── Line 2: command hint / transient hint (bottom-left) + context (right) ──
    const contextText = formatContextStatus(s.contextUsage, s.contextTokens, s.maxContextTokens);
    const contextWidth = visibleWidth(contextText);
    const contextColored = chalk.hex(contextColor(s.contextUsage * 100))(contextText);

    let line2: string;
    const bottomHint = this.commandHint ?? this.transientHint;
    if (bottomHint) {
      const maxHintWidth = Math.max(0, width - contextWidth - 1);
      const shownHint =
        visibleWidth(bottomHint) <= maxHintWidth
          ? bottomHint
          : truncateToWidth(bottomHint, maxHintWidth, '…');
      const hintWidth = visibleWidth(shownHint);
      const pad = Math.max(0, width - hintWidth - contextWidth);
      const hintColor = this.commandHint ? colors.primary : colors.warning;
      line2 =
        chalk.hex(hintColor).bold(shownHint) +
        ' '.repeat(pad) +
        contextColored;
    } else {
      const leftPad = Math.max(0, width - contextWidth);
      line2 = ' '.repeat(leftPad) + contextColored;
    }

    return [truncateToWidth(line1, width), truncateToWidth(line2, width)];
  }
}
