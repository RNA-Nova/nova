/**
 * /help 命令目录查看器（宿主本地命令 /help 的模态组件）。
 *
 * 分组展示（后端命令 / 提示词模板 / 技能 / 本地命令 / 扩展命令）——
 * 数据来自 commands/directory 的三源合并（与补全目录同一事实源）；
 * esc / ctrl+c / q 关闭。
 */
import { Text, matchesKey, truncateToWidth, type Component, type Focusable } from '@earendil-works/pi-tui';

import type { CommandDirectoryEntry } from '../../commands/directory.js';
import { colors } from '../../themes/index.js';

const GROUP_LABELS = {
  backend: '后端命令',
  prompt: '提示词模板（展开后发给模型）',
  skill: '技能（展开后发给模型）',
  local: '本地命令',
  slot: '扩展命令',
} as const;

type Group = keyof typeof GROUP_LABELS;

const GROUP_ORDER: Group[] = ['backend', 'prompt', 'skill', 'local', 'slot'];

/** 展示分组：行为类型优先（prompt/skill 单独成组），其余按来源。 */
function groupOf(entry: CommandDirectoryEntry): Group {
  if (entry.kind === 'prompt') return 'prompt';
  if (entry.kind === 'skill') return 'skill';
  return entry.source;
}

export class HelpViewer implements Component, Focusable {
  private _focused = false;
  private cachedWidth?: number;
  private cachedLines?: string[];

  constructor(
    private readonly entries: CommandDirectoryEntry[],
    private readonly onClose: () => void,
  ) {}

  get focused(): boolean {
    return this._focused;
  }

  set focused(value: boolean) {
    this._focused = value;
  }

  handleInput(data: string): void {
    if (matchesKey(data, 'escape') || matchesKey(data, 'ctrl+c') || data === 'q') {
      this.onClose();
    }
  }

  invalidate(): void {
    this.cachedWidth = undefined;
    this.cachedLines = undefined;
  }

  render(width: number): string[] {
    if (this.cachedLines && this.cachedWidth === width) return this.cachedLines;

    const lines: string[] = [];
    lines.push('');
    const title = colors.accent(` 命令（${this.entries.length}）`);
    const rule = '─'.repeat(Math.max(0, width - 16));
    lines.push(truncateToWidth(colors.borderMuted('───') + title + colors.borderMuted(rule), width));

    const nameWidth = Math.max(0, ...this.entries.map((e) => e.name.length));
    for (const group of GROUP_ORDER) {
      const entries = this.entries
        .filter((e) => groupOf(e) === group)
        .sort((a, b) => a.name.localeCompare(b.name));
      if (entries.length === 0) continue;
      lines.push('');
      lines.push(`  ${colors.muted(GROUP_LABELS[group])}`);
      for (const entry of entries) {
        const name = `/${entry.name.padEnd(nameWidth)}`;
        const desc = entry.description ?? '';
        lines.push(truncateToWidth(`  ${colors.accent(name)}  ${colors.dim(desc)}`, width));
      }
    }

    lines.push('');
    lines.push(truncateToWidth(`  ${colors.dim('esc 关闭 · ! 前缀执行 bash（!! 不进上下文）')}`, width));
    lines.push('');

    this.cachedWidth = width;
    this.cachedLines = lines;
    return lines;
  }
}
