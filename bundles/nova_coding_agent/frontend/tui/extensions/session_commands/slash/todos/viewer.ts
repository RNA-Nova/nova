/**
 * /todos 模态查看器组件（pi examples/extensions/todo.ts TodoListComponent 对位）。
 *
 * 纯展示模态：清单 + 进度行；esc / ctrl+c / q 关闭（done() 交还，无结果值）。
 * 主题经宿主子路径导出共享（jiti 别名 + ESM 缓存单例——tree 选择器同款通道）。
 */
import { Text, matchesKey, truncateToWidth, type Component, type Focusable } from '@earendil-works/pi-tui';

import { colors } from 'nova-client/modes/tui/themes/index';

export interface TodoViewItem {
  content: string;
  status: string;
}

const STATUS_ICONS: Record<string, string> = {
  pending: '○',
  in_progress: '◐',
  completed: '✓',
};

export class TodosViewer implements Component, Focusable {
  private _focused = false;
  private cachedWidth?: number;
  private cachedLines?: string[];

  constructor(
    private readonly todos: TodoViewItem[],
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
    const title = colors.accent(' Todos ');
    const rule = '─'.repeat(Math.max(0, width - 10));
    lines.push(truncateToWidth(colors.borderMuted('───') + title + colors.borderMuted(rule), width));
    lines.push('');

    if (this.todos.length === 0) {
      lines.push(truncateToWidth(`  ${colors.dim('清单为空——让 agent 用 todo 工具创建任务')}`, width));
    } else {
      const done = this.todos.filter((t) => t.status === 'completed').length;
      lines.push(truncateToWidth(`  ${colors.muted(`${done}/${this.todos.length} completed`)}`, width));
      lines.push('');
      for (const todo of this.todos) {
        const icon = STATUS_ICONS[todo.status] ?? '?';
        const styled =
          todo.status === 'completed'
            ? colors.success(icon)
            : todo.status === 'in_progress'
              ? colors.accent(icon)
              : colors.dim(icon);
        const text = todo.status === 'completed' ? colors.dim(todo.content) : todo.content;
        lines.push(truncateToWidth(`  ${styled} ${text}`, width));
      }
    }

    lines.push('');
    lines.push(truncateToWidth(`  ${colors.dim('esc 关闭')}`, width));
    lines.push('');

    this.cachedWidth = width;
    this.cachedLines = lines;
    return lines;
  }
}
