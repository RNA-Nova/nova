/**
 * scoped-models 面板（pi scoped-models-selector 对位）。
 *
 * 归属：官方 bundle 的 ui/ 段（包自持命令 UI——dogfood：官方与第三方同机制；
 * 从 nova-client 宿主迁入）。宿主部件经 'nova-tui/modes/tui/*' 子路径导出
 * 共享（jiti 别名 + 原生 ESM 缓存，主题/键位单例与宿主同实例）。
 *
 * scoped 池 = ctrl+p 循环模型的启用集与循环顺序（session 级配置）。
 * 面板行为（pi 对位）：
 * - 启用行在前（循环顺序），未启用行在后（provider/id 序）；
 * - space/enter 切换启用（启用追加到循环序末尾；禁用移除）；
 * - alt+↑/↓ 调整启用项的循环顺序；
 * - ctrl+a 全启用（有搜索词时仅过滤结果）、ctrl+x 全清除；
 * - ctrl+s 保存（编排写 setScopedModels RPC——**仅保存时写**，pi "session-only
 *   until saved" 语义）；esc 放弃本地编辑；有未保存改动时标题带 (unsaved)；
 * - 可打印字符进搜索框（fuzzy 过滤显示，不影响启用状态）。
 */

import {
  Container,
  Input,
  Spacer,
  Text,
  fuzzyFilter,
  getKeybindings,
  matchesKey,
  type Focusable,
} from '@earendil-works/pi-tui';

import { DynamicBorder } from 'nova-tui/modes/tui/components/layout/dynamic-border';
import { colors } from 'nova-tui/modes/tui/themes/index';

/** 面板条目（启用状态 + 展示元信息）。 */
export interface ScopedModelRow {
  /** `provider/id` 键。 */
  key: string;
  provider: string;
  id: string;
  name: string;
  thinkingLevel: string | null;
}

export interface ScopedModelsSelectorCallbacks {
  /** ctrl+s 保存（orderedKeys 为循环顺序）。 */
  onSave: (orderedKeys: string[]) => void;
  onCancel: () => void;
}

/** 面板初始状态（编排组装：scoped 序在前 + 未启用按序在后）。 */
export function buildScopedRows(
  scoped: Array<{ provider: string; id: string; thinkingLevel: string | null }>,
  all: Array<{ provider: string; id: string; name: string }>,
): { enabled: ScopedModelRow[]; disabled: ScopedModelRow[] } {
  const byKey = new Map(all.map((m) => [`${m.provider}/${m.id}`, m]));
  const scopedKeys = new Set(scoped.map((m) => `${m.provider}/${m.id}`));
  const enabled: ScopedModelRow[] = [];
  for (const m of scoped) {
    const key = `${m.provider}/${m.id}`;
    const found = byKey.get(key);
    enabled.push({
      key,
      provider: m.provider,
      id: m.id,
      name: found?.name ?? m.id,
      thinkingLevel: m.thinkingLevel,
    });
  }
  const disabled = all
    .filter((m) => !scopedKeys.has(`${m.provider}/${m.id}`))
    .map((m) => ({
      key: `${m.provider}/${m.id}`,
      provider: m.provider,
      id: m.id,
      name: m.name,
      thinkingLevel: null,
    }));
  return { enabled, disabled };
}

export class ScopedModelsSelector extends Container implements Focusable {
  private _focused = false;
  private enabled: ScopedModelRow[];
  private disabled: ScopedModelRow[];
  private display: Array<{ row: ScopedModelRow; on: boolean }> = [];
  private selectedIndex = 0;
  private readonly searchInput = new Input();
  private readonly body = new Container();
  private readonly maxVisible = 12;
  private readonly savedKeys: string[];
  private dirty = false;

  constructor(
    scoped: Array<{ provider: string; id: string; thinkingLevel: string | null }>,
    all: Array<{ provider: string; id: string; name: string }>,
    private readonly callbacks: ScopedModelsSelectorCallbacks,
  ) {
    super();
    const initial = buildScopedRows(scoped, all);
    this.enabled = initial.enabled;
    this.disabled = initial.disabled;
    this.savedKeys = this.enabled.map((row) => row.key);

    this.addChild(new DynamicBorder());
    this.addChild(new Text(colors.accent(' Scoped 模型池 '), 0, 0));
    this.addChild(this.searchInput);
    this.addChild(this.body);
    this.addChild(new DynamicBorder());
    this.applyFilter();
    this.rebuild();
  }

  get focused(): boolean {
    return this._focused;
  }
  set focused(value: boolean) {
    this._focused = value;
    this.searchInput.focused = value;
  }

  private get selected(): { row: ScopedModelRow; on: boolean } | undefined {
    return this.display[this.selectedIndex];
  }

  private get dirtyMark(): string {
    return this.dirty ? colors.warning(' (unsaved)') : '';
  }

  private applyFilter(): void {
    const query = this.searchInput.getValue().trim();
    const all: Array<{ row: ScopedModelRow; on: boolean }> = [
      ...this.enabled.map((row) => ({ row, on: true })),
      ...this.disabled.map((row) => ({ row, on: false })),
    ];
    this.display = query
      ? fuzzyFilter(all, query, (item) => `${item.row.provider}/${item.row.id} ${item.row.name}`)
      : all;
    this.selectedIndex = Math.max(0, Math.min(this.selectedIndex, this.display.length - 1));
  }

  private rebuild(): void {
    this.applyFilter();
    this.body.clear();
    this.body.addChild(
      new Text(
        colors.dim(
          ' space 切换 · alt+↑↓ 排序 · ctrl+a 全启用 · ctrl+x 全清 · ctrl+s 保存 · esc 取消',
        ) + this.dirtyMark,
        0,
        0,
      ),
    );
    this.body.addChild(new Spacer(1));
    if (this.display.length === 0) {
      this.body.addChild(new Text(colors.warning('  无匹配模型'), 0, 0));
      return;
    }
    const start = Math.max(
      0,
      Math.min(this.selectedIndex - Math.floor(this.maxVisible / 2), this.display.length - this.maxVisible),
    );
    const windowRows = this.display.slice(start, start + this.maxVisible);
    windowRows.forEach((item, offset) => {
      const isSelected = start + offset === this.selectedIndex;
      const order = item.on ? `${this.enabled.indexOf(item.row) + 1}. ` : '   ';
      const box = item.on ? '[x] ' : '[ ] ';
      const line = `${isSelected ? '› ' : '  '}${order}${box}${item.row.key}${colors.dim(`  ${item.row.name}`)}`;
      this.body.addChild(new Text(isSelected ? colors.accent(line) : line, 0, 0));
    });
    this.body.addChild(new Spacer(1));
    this.body.addChild(
      new Text(colors.dim(` 启用 ${this.enabled.length} · 共 ${this.enabled.length + this.disabled.length}`), 0, 0),
    );
  }

  private markDirty(): void {
    this.dirty =
      JSON.stringify(this.enabled.map((row) => row.key)) !== JSON.stringify(this.savedKeys);
  }

  private move(delta: number): void {
    if (this.display.length === 0) return;
    this.selectedIndex = (this.selectedIndex + delta + this.display.length) % this.display.length;
    this.rebuild();
  }

  /** space/enter：切换选中行启用状态。 */
  private toggleSelected(): void {
    const item = this.selected;
    if (!item) return;
    if (item.on) {
      this.enabled = this.enabled.filter((row) => row.key !== item.row.key);
      this.disabled.push(item.row);
    } else {
      this.disabled = this.disabled.filter((row) => row.key !== item.row.key);
      this.enabled.push(item.row);
    }
    this.markDirty();
    this.rebuild();
  }

  /** alt+↑/↓：调整启用项循环顺序（仅启用行可移动）。 */
  private reorder(delta: number): void {
    const item = this.selected;
    if (!item?.on) return;
    const index = this.enabled.indexOf(item.row);
    const target = index + delta;
    if (target < 0 || target >= this.enabled.length) return;
    const next = [...this.enabled];
    [next[index], next[target]] = [next[target], next[index]];
    this.enabled = next;
    this.markDirty();
    this.rebuild();
  }

  private enableAll(): void {
    const query = this.searchInput.getValue().trim();
    // pi 语义：有搜索词时仅启用过滤结果
    const pool = query ? this.display.filter((item) => !item.on).map((item) => item.row) : this.disabled;
    for (const row of pool) {
      this.enabled.push(row);
    }
    const enabledKeys = new Set(this.enabled.map((row) => row.key));
    this.disabled = this.disabled.filter((row) => !enabledKeys.has(row.key));
    this.markDirty();
    this.rebuild();
  }

  private clearAll(): void {
    this.disabled = [...this.disabled, ...this.enabled];
    this.enabled = [];
    this.markDirty();
    this.rebuild();
  }

  handleInput(data: string): void {
    const kb = getKeybindings();
    if (kb.matches(data, 'tui.select.cancel')) {
      this.callbacks.onCancel();
      return;
    }
    if (kb.matches(data, 'tui.select.up')) {
      this.move(-1);
      return;
    }
    if (kb.matches(data, 'tui.select.down')) {
      this.move(1);
      return;
    }
    if (data === ' ' || kb.matches(data, 'tui.select.confirm') || data === '\n') {
      this.toggleSelected();
      return;
    }
    if (matchesKey(data, 'alt+up')) {
      this.reorder(-1);
      return;
    }
    if (matchesKey(data, 'alt+down')) {
      this.reorder(1);
      return;
    }
    if (matchesKey(data, 'ctrl+a')) {
      this.enableAll();
      return;
    }
    if (matchesKey(data, 'ctrl+x')) {
      this.clearAll();
      return;
    }
    if (matchesKey(data, 'ctrl+s')) {
      this.callbacks.onSave(this.enabled.map((row) => row.key));
      return;
    }
    // 其余输入 → 搜索框
    this.searchInput.handleInput(data);
    this.rebuild();
  }
}
