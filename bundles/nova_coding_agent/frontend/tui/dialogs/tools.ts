/**
 * tools 工具开关面板（dialog:tools——pi tools.ts 的 SettingsList 对位）。
 *
 * 契约：
 * - 入参 params：{ tools: [{name, label, description, active}...] }；
 * - done({active: [name...]})：激活工具名数组（按入参原序归集）；
 *   done(undefined) = 取消。
 *
 * 键位：↑↓ 移动 · space 切换 · enter 提交 · esc 取消。
 * 视觉：复用选择器/面板惯用法（─ 边框 + › 光标行 + [x]/[ ] 复选行，
 * scoped-models 面板为最近参照）；配色经工厂 env 注入（RegionEnv.colors）。
 */
import {
  Key,
  matchesKey,
  visibleWidth,
  wrapTextWithAnsi,
  type Component,
  type Focusable,
} from '@earendil-works/pi-tui';

import { colors as themeColors } from 'nova-client/modes/tui/themes/index';

/** 面板条目（工具开关状态 + 展示元信息）。 */
export interface ToolsDialogTool {
  name: string;
  label: string;
  description: string;
  active: boolean;
}

type DialogColors = typeof themeColors;

/** 工具开关面板（复选列表——space 切换激活态，enter 提交激活名集）。 */
export class ToolsDialog implements Component, Focusable {
  private selectedIndex = 0;
  private readonly actives: boolean[]; // 与 tools 同序的本地编辑态
  private cachedWidth?: number;
  private cachedLines?: string[];
  private _focused = false;

  constructor(
    private readonly tools: ToolsDialogTool[],
    private readonly colors: DialogColors,
    private readonly onDone: (result?: { active: string[] }) => void,
  ) {
    this.actives = tools.map((t) => t.active);
  }

  get focused(): boolean {
    return this._focused;
  }

  set focused(value: boolean) {
    this._focused = value;
  }

  private refresh(): void {
    this.cachedLines = undefined;
    this.cachedWidth = undefined;
  }

  invalidate(): void {
    this.refresh();
  }

  handleInput(data: string): void {
    if (matchesKey(data, Key.up)) {
      this.selectedIndex = Math.max(0, this.selectedIndex - 1);
      this.refresh();
      return;
    }
    if (matchesKey(data, Key.down)) {
      this.selectedIndex = Math.min(Math.max(0, this.tools.length - 1), this.selectedIndex + 1);
      this.refresh();
      return;
    }
    if (data === ' ') {
      if (this.tools.length > 0) {
        this.actives[this.selectedIndex] = !this.actives[this.selectedIndex];
        this.refresh();
      }
      return;
    }
    if (matchesKey(data, Key.enter)) {
      // 提交激活工具名数组（入参原序）
      this.onDone({ active: this.tools.filter((_, i) => this.actives[i]).map((t) => t.name) });
      return;
    }
    if (matchesKey(data, Key.escape)) {
      this.onDone(undefined);
    }
  }

  render(width: number): string[] {
    if (this.cachedLines && this.cachedWidth === width) return this.cachedLines;

    const lines: string[] = [];
    const renderWidth = Math.max(1, width);
    const colors = this.colors;

    const addWrappedWithPrefix = (prefix: string, text: string) => {
      const prefixWidth = visibleWidth(prefix);
      if (prefixWidth >= renderWidth) {
        lines.push(...wrapTextWithAnsi(prefix + text, renderWidth));
        return;
      }
      const wrapped = wrapTextWithAnsi(text, renderWidth - prefixWidth);
      const continuationPrefix = ' '.repeat(prefixWidth);
      for (let i = 0; i < wrapped.length; i++) {
        lines.push(`${i === 0 ? prefix : continuationPrefix}${wrapped[i]}`);
      }
    };

    lines.push(colors.accent('─'.repeat(renderWidth)));
    addWrappedWithPrefix(' ', colors.accent('工具开关'));
    lines.push('');

    if (this.tools.length === 0) {
      addWrappedWithPrefix('  ', colors.warning('无可用工具'));
    }
    for (let i = 0; i < this.tools.length; i++) {
      const tool = this.tools[i];
      const selected = i === this.selectedIndex;
      const box = this.actives[i] ? '[x] ' : '[ ] ';
      const description = tool.description ? colors.muted(` — ${tool.description}`) : '';
      const line = `${box}${tool.label}${description}`;
      addWrappedWithPrefix(selected ? colors.accent('› ') : '  ', selected ? colors.accent(line) : line);
    }

    lines.push('');
    addWrappedWithPrefix(
      ' ',
      colors.dim(`↑↓ 移动 · space 切换 · enter 提交 · esc 取消（激活 ${this.actives.filter(Boolean).length}/${this.tools.length}）`),
    );
    lines.push(colors.accent('─'.repeat(renderWidth)));

    this.cachedWidth = width;
    this.cachedLines = lines;
    return lines;
  }
}

/** dialog:tools 工厂（ExtensionUIAPI.registerDialog 的注册形态）。 */
export function toolsDialogFactory(
  env: unknown,
  params: Record<string, unknown>,
  done: (result?: unknown) => void,
): Component {
  const colors = (env as { colors?: DialogColors }).colors ?? themeColors;
  const rawTools = Array.isArray(params.tools) ? params.tools : [];
  const tools: ToolsDialogTool[] = rawTools
    .filter((t): t is Record<string, unknown> => typeof t === 'object' && t !== null)
    .map((t) => {
      const name = typeof t.name === 'string' ? t.name : '';
      const label = typeof t.label === 'string' && t.label ? t.label : name;
      return {
        name,
        label,
        description: typeof t.description === 'string' ? t.description : '',
        active: t.active === true,
      };
    })
    .filter((t) => t.name.length > 0);
  return new ToolsDialog(tools, colors, (result) => done(result));
}
