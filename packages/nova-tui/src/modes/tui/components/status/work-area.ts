/**
 * 工作区视图（status 槽位的 working 态——超越单行 spinner 的丰富形态）。
 *
 * 传统 working 行是一次性静态文案（spinner + "Working…" 到 turn 结束）；
 * 本组件把 status 槽位升级为"工作区"，一目了然四要素：
 *
 * - **行 1（在干什么 · 干了多久 · 消耗多少 · 当前阶段）**：
 *   `⠋ <活动>… (<计时> · ↓ ~<输出量> · [thinking] · [tools:n] · [agents:n])`——
 *   活动为当前工具名（无工具取 workingMessage）；输出量为字符数/4 估算
 *   （~ 前缀明示，真实 usage 完结后进 footer）；thinking 标记在模型正流式
 *   输出思考时出现；tools/agents 计数器仅在 >1/>0 时出现（agents 取运行中
 *   subagent 卡片内的在跑子代理数）。
 * - **清单区（打算干什么）**：最新 todo 清单逐项状态行——首行 └ 连接符，
 *   完成 ✓ 绿色 + 删除线、在跑 ■ 红色高亮、待办 □；封顶 MAX_TODO_LINES
 *   行，溢出合并"… 还有 n 项"。数据来自 store 最新 todo 卡片（零 RPC）。
 * - **流光（Claude Code 对位）**：行 1 亮度扫过——accent 高亮窗口按
 *   100ms 节拍左→右扫过文本（applyShimmer），spinner 帧自旋；数据
 *   （计时/输出量/清单）每 500ms 重算一次，与流光共用同一定时器。
 *
 * dispose 停表。
 */

import chalk from 'chalk';

import { Container, Text, type LoaderIndicatorOptions, type TUI } from '@earendil-works/pi-tui';

import type { MirrorStore, TranscriptEntry } from 'nova-tui';

import { colors } from '../../themes/index.js';

/** todo 清单项（todo 工具 details.todos 的线形状）。 */
export interface TodoViewItem {
  content: string;
  status: string;
}

/** 清单区最大显示行数（溢出合并为"… 还有 n 项"——防吃 transcript 视口）。 */
const MAX_TODO_LINES = 6;

/** 从 transcript 条目找最新 todo 清单（以最新一条 todo 调用为准）。 */
export function latestTodos(entries: readonly TranscriptEntry[]): TodoViewItem[] | undefined {
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry?.kind === 'toolCall' && entry.card.toolName === 'todo') {
      const details = (entry.card.result?.details ?? entry.card.partial?.details) as
        | { todos?: unknown }
        | undefined;
      if (Array.isArray(details?.todos)) return details.todos as TodoViewItem[];
      return undefined;
    }
  }
  return undefined;
}

/** 流式输出量（字符数）：最近一条 assistant 处于 streaming 时取其文本长度。 */
export function liveOutputChars(entries: readonly TranscriptEntry[]): number {
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry?.kind !== 'assistant') continue;
    return entry.streaming ? entry.text.length : 0;
  }
  return 0;
}

/** 最近一条 assistant 是否正在流式输出 thinking。 */
export function hasStreamingThinking(entries: readonly TranscriptEntry[]): boolean {
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry?.kind !== 'assistant') continue;
    return entry.streaming === true && (entry.thinking ?? '').trim().length > 0;
  }
  return false;
}

/** 当前在跑的工具调用（取最近一条）。 */
export function runningToolEntry(
  entries: readonly TranscriptEntry[],
): Extract<TranscriptEntry, { kind: 'toolCall' }> | undefined {
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (
      entry?.kind === 'toolCall' &&
      (entry.card.status === 'running' || entry.card.status === 'streaming')
    ) {
      return entry as Extract<TranscriptEntry, { kind: 'toolCall' }>;
    }
  }
  return undefined;
}

/** 在跑工具数（含 streaming 参数累积中的）。 */
export function runningToolCount(entries: readonly TranscriptEntry[]): number {
  return entries.filter(
    (e) =>
      e.kind === 'toolCall' && (e.card.status === 'running' || e.card.status === 'streaming'),
  ).length;
}

/** 在跑子代理数：运行中 subagent 卡片内 exit_code=-1 的结果计数（无结果帧按 1 计）。 */
export function runningSubagentCount(entries: readonly TranscriptEntry[]): number {
  let count = 0;
  for (const entry of entries) {
    if (
      entry?.kind !== 'toolCall' ||
      entry.card.toolName !== 'subagent' ||
      (entry.card.status !== 'running' && entry.card.status !== 'streaming')
    ) {
      continue;
    }
    const details = (entry.card.partial?.details ?? entry.card.result?.details) as
      | { results?: Array<{ exit_code?: number }> }
      | undefined;
    if (details?.results !== undefined) {
      count += details.results.filter((r) => r.exit_code === -1).length;
    } else {
      count += 1;
    }
  }
  return count;
}

/** token 估算格式化（字符数/4，与后端 estimate_context_tokens 同源）。 */
export function formatTokenEstimate(chars: number): string {
  const tokens = Math.round(chars / 4);
  if (tokens < 1000) return String(tokens);
  if (tokens < 10000) return `${(tokens / 1000).toFixed(1)}k`;
  return `${Math.round(tokens / 1000)}k`;
}

/** 计时格式化（与工具卡片计时行同款粒度）。 */
export function formatElapsed(ms: number): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${s}s`;
}

const TICK_MS = 100;
/** 每 N 个 tick 重算一次数据（行 1 文案 + 清单）——流光 100ms、数据 500ms。 */
const DATA_REFRESH_TICKS = 5;
/** 流光亮窗宽度（字符数）。 */
const SHIMMER_WINDOW = 6;

const DEFAULT_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

/**
 * 流光扫过文本：``pos`` 起 ``window`` 个字符 accent 高亮，其余 dim
 * （Claude Code working 行的亮度扫过效果对位；pos 越界自动截断）。
 */
export function applyShimmer(text: string, pos: number, window: number): string {
  const chars = [...text];
  const start = Math.max(0, Math.min(pos, chars.length));
  const end = Math.min(chars.length, pos + window);
  const before = chars.slice(0, start).join('');
  const mid = chars.slice(start, end).join('');
  const after = chars.slice(end).join('');
  return colors.dim(before) + colors.accent(mid) + colors.dim(after);
}

export class WorkAreaView extends Container {
  readonly kind = 'working' as const;
  private readonly startedAt = Date.now();
  private readonly timer: NodeJS.Timeout;
  private readonly display: Text;
  private readonly frames: string[];
  private readonly workingMessage?: string;
  private message: string;
  private tickCount = 0;
  private todoLines: Text[] = [];

  constructor(
    tui: TUI,
    private readonly store: MirrorStore,
    options: { message?: string; indicator?: LoaderIndicatorOptions } = {},
    private readonly onTick: () => void,
  ) {
    super();
    this.workingMessage = options.message;
    this.frames =
      options.indicator?.frames && options.indicator.frames.length > 0
        ? [...options.indicator.frames]
        : [...DEFAULT_FRAMES];
    this.message = this.line1();
    this.display = new Text(this.lineDisplay(), 1, 0);
    this.addChild(this.display);
    this.refreshTodoLines();
    const intervalMs =
      options.indicator?.intervalMs && options.indicator.intervalMs > 0
        ? options.indicator.intervalMs
        : TICK_MS;
    this.timer = setInterval(() => this.tick(), intervalMs);
  }

  private tick(): void {
    this.tickCount += 1;
    if (this.tickCount % DATA_REFRESH_TICKS === 0) {
      this.message = this.line1();
      this.refreshTodoLines();
    }
    this.display.setText(this.lineDisplay());
    this.onTick();
  }

  /** 行 1 的显示形态：spinner 帧 + 流光文本。 */
  private lineDisplay(): string {
    const frame = this.frames[this.tickCount % this.frames.length];
    const pos = (this.tickCount % (this.message.length + SHIMMER_WINDOW)) - SHIMMER_WINDOW;
    return `${colors.accent(frame)} ${applyShimmer(this.message, pos, SHIMMER_WINDOW)}`;
  }

  /** 行 1：<活动>… (<计时> · ↓ ~<输出量> · [thinking] · [tools:n] · [agents:n])。 */
  private line1(): string {
    const entries = this.store.entries;
    const tool = runningToolEntry(entries);
    const base = tool ? `Running ${tool.card.toolName}…` : (this.workingMessage ?? 'Working…');
    const parts = [formatElapsed(Date.now() - this.startedAt)];
    const chars = liveOutputChars(entries);
    // ~ 前缀明示估算口径（真实 usage 在 message_end 才到，完结后进 footer 累计）
    if (chars > 0) parts.push(`↓ ~${formatTokenEstimate(chars)}`);
    if (hasStreamingThinking(entries)) parts.push('thinking');
    const tools = runningToolCount(entries);
    if (tools > 1) parts.push(`tools:${tools}`);
    const agents = runningSubagentCount(entries);
    if (agents > 0) parts.push(`agents:${agents}`);
    return `${base} (${parts.join(' · ')})`;
  }

  /** 清单区：逐项状态行（首行 └ 连接符；完成 ✓+删除线 / 在跑 ■ / 待办 □）。 */
  private refreshTodoLines(): void {
    const todos = latestTodos(this.store.entries);
    for (const line of this.todoLines) this.removeChild(line);
    this.todoLines = [];
    if (todos === undefined || todos.length === 0) return;

    const done = todos.filter((t) => t.status === 'completed').length;
    if (done === todos.length) {
      const line = new Text(colors.dim(`  ${colors.success('✓')} ${done}/${todos.length} 全部完成`), 1, 0);
      this.todoLines.push(line);
      this.addChild(line);
      return;
    }

    const visible = todos.slice(0, MAX_TODO_LINES);
    visible.forEach((todo, index) => {
      const prefix = index === 0 ? '└ ' : '  ';
      let text: string;
      if (todo.status === 'completed') {
        text =
          colors.dim(`${prefix}${colors.success('✓')} `) +
          chalk.strikethrough(colors.dim(todo.content));
      } else if (todo.status === 'in_progress') {
        text = colors.error(`${prefix}■ ${todo.content}`);
      } else {
        text = colors.muted(`${prefix}□ ${todo.content}`);
      }
      const line = new Text(text, 1, 0);
      this.todoLines.push(line);
      this.addChild(line);
    });
    if (todos.length > visible.length) {
      const line = new Text(colors.dim(`  … 还有 ${todos.length - visible.length} 项`), 1, 0);
      this.todoLines.push(line);
      this.addChild(line);
    }
  }

  dispose(): void {
    clearInterval(this.timer);
  }
}
