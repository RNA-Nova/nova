/**
 * 会话选择器（pi session-selector 对位：内联删除/重命名/作用域/排序/搜索语法）。
 *
 * 归属：官方 bundle 的 ui/ 段（包自持命令 UI——dogfood：官方与第三方同机制；
 * 从 nova-client 宿主迁入）。包自持的原因（与 /tree 同一判决）：per-item
 * 动作键（ctrl+d 删除确认 / ctrl+r 重命名 / tab 作用域）无法经反向原语
 * select 表达。宿主部件经 'nova-tui/modes/tui/*' 子路径导出共享
 * （jiti 别名 + 原生 ESM 缓存，主题/键位单例与宿主同实例）。
 * 后端 /resume 命令保留作 headless 回退。
 *
 * 键位：
 * - ↑/↓ 环绕移动；可打印字符进搜索框；
 * - tab：切换 current ⇄ all 作用域（编排重载后 setItems）；
 * - ctrl+s：排序循环 threaded → recent → relevance；
 * - ctrl+n：只看命名会话（toggle）；ctrl+p：行尾显示文件路径（toggle）；
 * - ctrl+d：删除确认态（吞键——只响应 enter 确认 / esc 取消）；
 * - ctrl+r：重命名态（Input 预填当前名；空名=清除，对齐后端语义）；
 * - enter 选中；esc 取消（模态优先退模态）。
 *
 * 搜索语法（pi session-selector-search 对位）：``re:<pattern>`` 大小写不敏感
 * 正则（非法→空结果）；``"phrase"`` 空白归一精确子串；裸 token → fuzzy。
 * threaded 排序仅在无搜索词时启用（有查询回退 relevance/recent——pi 同款）。
 *
 * 残余挂账：无。
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

/** 会话条目（listSessions 富字段的消费面）。 */
export interface SessionItem {
  path: string;
  name: string | null;
  firstMessage: string;
  messageCount: number;
  /** epoch 秒。 */
  modified: number;
  cwd: string;
  parentSessionPath: string | null;
}

/** 排序模式（pi 对位：threaded 树形 / recent 时间 / relevance 相关度）。 */
export type SessionSortMode = 'threaded' | 'recent' | 'relevance';

/** 渲染行（depth 为 threaded 模式的树形缩进层级）。 */
export interface SessionRow extends SessionItem {
  depth: number;
}

export interface SessionSelectorCallbacks {
  onSelect: (path: string) => void;
  onCancel: () => void;
  onDelete: (path: string) => void;
  onRename: (path: string, name: string) => void;
  /** 作用域切换（编排重载列表后调 setItems）。 */
  onScopeChange: (scope: 'current' | 'all') => void;
}

/** pi formatSessionDate 对位：now/s/m/h/d/w/mo/y 缩写。 */
export function formatAge(modifiedEpochSeconds: number, nowMs = Date.now()): string {
  const seconds = Math.max(0, Math.floor(nowMs / 1000 - modifiedEpochSeconds));
  if (seconds < 45) return 'now';
  if (seconds < 90) return '1m';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  const weeks = Math.floor(days / 7);
  if (weeks < 5) return `${weeks}w`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo`;
  return `${Math.floor(days / 365)}y`;
}

/** 条目标题：名字优先，否则首条消息预览（控制字符清洗）。 */
export function sessionTitle(item: SessionItem): string {
  const raw = item.name ?? item.firstMessage ?? '';
  const cleaned = raw.replace(/[\x00-\x1f]+/g, ' ').trim();
  return cleaned === '' ? '(无内容会话)' : cleaned;
}

/** 搜索过滤（pi 搜索语法：re: 正则 / "phrase" 精确 / 裸 token fuzzy）。 */
export function filterSessions(
  items: SessionItem[],
  query: string,
  namedOnly: boolean,
): SessionItem[] {
  let pool = namedOnly ? items.filter((item) => item.name !== null) : items;
  const trimmed = query.trim();
  if (!trimmed) return pool;
  if (trimmed.startsWith('re:')) {
    try {
      const regex = new RegExp(trimmed.slice(3), 'i');
      return pool.filter((item) => regex.test(sessionTitle(item)));
    } catch {
      return []; // 非法正则：空结果（pi 语义）
    }
  }
  const phraseMatch = /^"([^"]*)"$/.exec(trimmed);
  if (phraseMatch) {
    const phrase = phraseMatch[1].replace(/\s+/g, ' ').toLowerCase();
    return pool.filter((item) =>
      sessionTitle(item).replace(/\s+/g, ' ').toLowerCase().includes(phrase),
    );
  }
  return fuzzyFilter(pool, trimmed, (item) => sessionTitle(item));
}

/** 相关度打分（relevance 模式）：子串位置优先，fuzzy 命中兜底；小分靠前。 */
function relevanceScore(item: SessionItem, query: string): number {
  const title = sessionTitle(item).toLowerCase();
  const needle = query.toLowerCase();
  const index = title.indexOf(needle);
  if (index >= 0) return index * 0.1;
  // 子序列命中：跨度越小越相关
  let cursor = 0;
  let span = 0;
  for (const ch of needle) {
    const found = title.indexOf(ch, cursor);
    if (found < 0) return Number.POSITIVE_INFINITY;
    if (cursor > 0) span += found - cursor;
    cursor = found + 1;
  }
  return 10 + span;
}

/** 视图管线：过滤 → 排序 → 渲染行（threaded 组树）。 */
export function applySessionView(
  items: SessionItem[],
  options: { query: string; namedOnly: boolean; sort: SessionSortMode },
): SessionRow[] {
  const filtered = filterSessions(items, options.query, options.namedOnly);
  const hasQuery = options.query.trim() !== '';

  if (options.sort === 'threaded' && !hasQuery) {
    // 树形：父会话不在列表中的提升为根；根按 modified 倒序；子随父后缩进
    const byPath = new Map(filtered.map((item) => [item.path, item]));
    const childrenOf = new Map<string, SessionItem[]>();
    const roots: SessionItem[] = [];
    for (const item of filtered) {
      const parent = item.parentSessionPath;
      if (parent && byPath.has(parent)) {
        const siblings = childrenOf.get(parent) ?? [];
        siblings.push(item);
        childrenOf.set(parent, siblings);
      } else {
        roots.push(item);
      }
    }
    const byModifiedDesc = (a: SessionItem, b: SessionItem) => b.modified - a.modified;
    roots.sort(byModifiedDesc);
    const rows: SessionRow[] = [];
    const visit = (item: SessionItem, depth: number) => {
      rows.push({ ...item, depth });
      for (const child of (childrenOf.get(item.path) ?? []).sort(byModifiedDesc)) {
        visit(child, depth + 1);
      }
    };
    for (const root of roots) visit(root, 0);
    return rows;
  }

  if (options.sort === 'relevance' && hasQuery) {
    return filtered
      .map((item) => ({ item, score: relevanceScore(item, options.query.trim()) }))
      .filter((row) => Number.isFinite(row.score))
      .sort((a, b) => a.score - b.score || b.item.modified - a.item.modified)
      .map((row) => ({ ...row.item, depth: 0 }));
  }

  // recent（relevance 无查询时也回退到此——pi 语义）
  return [...filtered]
    .sort((a, b) => b.modified - a.modified)
    .map((item) => ({ ...item, depth: 0 }));
}

export class SessionSelector extends Container implements Focusable {
  private _focused = false;
  private items: SessionItem[];
  private rows: SessionRow[];
  private selectedIndex = 0;
  private scope: 'current' | 'all' = 'current';
  private sortMode: SessionSortMode = 'recent';
  private namedOnly = false;
  private showPath = false;
  private mode: 'list' | 'confirmDelete' | 'rename' = 'list';
  private readonly searchInput = new Input();
  private readonly renameInput = new Input();
  private readonly body = new Container();
  private readonly maxVisible = 10;

  constructor(
    items: SessionItem[],
    private readonly callbacks: SessionSelectorCallbacks,
  ) {
    super();
    this.items = items;
    this.rows = applySessionView(items, this.viewOptions);

    this.addChild(new DynamicBorder());
    this.addChild(new Text(colors.accent(' 会话 '), 0, 0));
    this.addChild(this.searchInput);
    this.addChild(this.body);
    this.addChild(new DynamicBorder());

    this.renameInput.onSubmit = () => this.commitRename();
    this.renameInput.onEscape = () => this.exitModes();
    this.rebuild();
  }

  get focused(): boolean {
    return this._focused;
  }
  set focused(value: boolean) {
    this._focused = value;
    this.searchInput.focused = value && this.mode === 'list';
    this.renameInput.focused = value && this.mode === 'rename';
  }

  /** 编排重载/变更后刷新列表（保选中项按 path 跟随）。 */
  setItems(items: SessionItem[]): void {
    const currentPath = this.selected?.path;
    this.items = items;
    this.applyView();
    if (currentPath) {
      const index = this.rows.findIndex((row) => row.path === currentPath);
      if (index >= 0) this.selectedIndex = index;
    }
    this.rebuild();
  }

  get currentScope(): 'current' | 'all' {
    return this.scope;
  }

  private get selected(): SessionRow | undefined {
    return this.rows[this.selectedIndex];
  }

  private get viewOptions(): { query: string; namedOnly: boolean; sort: SessionSortMode } {
    return {
      query: this.searchInput.getValue(),
      namedOnly: this.namedOnly,
      sort: this.sortMode,
    };
  }

  private applyView(): void {
    this.rows = applySessionView(this.items, this.viewOptions);
    this.selectedIndex = Math.max(0, Math.min(this.selectedIndex, this.rows.length - 1));
  }

  /** 状态变化后重建可见区。 */
  private rebuild(): void {
    this.body.clear();
    if (this.mode === 'confirmDelete') {
      const item = this.selected;
      this.body.addChild(
        new Text(colors.error(` 删除会话？ ${item ? sessionTitle(item) : ''}`), 0, 0),
      );
      this.body.addChild(new Spacer(1));
      this.body.addChild(new Text(colors.error(' enter 确认删除 · esc 取消'), 0, 0));
      return;
    }
    if (this.mode === 'rename') {
      this.body.addChild(new Text(colors.dim(' 重命名（留空清除名字）：'), 0, 0));
      this.body.addChild(this.renameInput);
      this.body.addChild(new Spacer(1));
      this.body.addChild(new Text(colors.dim(' enter 保存 · esc 返回'), 0, 0));
      return;
    }
    // list 模式
    this.body.addChild(
      new Text(
        colors.dim(
          ` ↑↓ 移动 · tab 作用域(${this.scope === 'current' ? '当前目录' : '全部'}) · ctrl+s 排序(${this.sortMode}) · ctrl+n 命名${this.namedOnly ? '✓' : ''} · ctrl+p 路径${this.showPath ? '✓' : ''} · ctrl+d 删除 · ctrl+r 重命名`,
        ),
        0,
        0,
      ),
    );
    this.body.addChild(new Spacer(1));
    if (this.rows.length === 0) {
      this.body.addChild(new Text(colors.warning('  无匹配会话'), 0, 0));
      return;
    }
    const start = Math.max(
      0,
      Math.min(this.selectedIndex - Math.floor(this.maxVisible / 2), this.rows.length - this.maxVisible),
    );
    const windowRows = this.rows.slice(start, start + this.maxVisible);
    windowRows.forEach((row, offset) => {
      const isSelected = start + offset === this.selectedIndex;
      const indent = '  '.repeat(row.depth);
      const meta = `${row.messageCount} 条 · ${formatAge(row.modified)}`;
      const pathPart = this.showPath ? colors.dim(`  ${row.path}`) : '';
      const line = `${isSelected ? '› ' : '  '}${indent}${sessionTitle(row)}${colors.dim(`  ${meta}`)}${pathPart}`;
      this.body.addChild(new Text(isSelected ? colors.accent(line) : line, 0, 0));
    });
    if (this.rows.length > this.maxVisible) {
      this.body.addChild(
        new Text(colors.dim(` (${this.selectedIndex + 1}/${this.rows.length})`), 0, 0),
      );
    }
  }

  private move(delta: number): void {
    if (this.rows.length === 0) return;
    this.selectedIndex = (this.selectedIndex + delta + this.rows.length) % this.rows.length;
    this.rebuild();
  }

  private exitModes(): void {
    this.mode = 'list';
    this.rebuild();
  }

  private commitRename(): void {
    const item = this.selected;
    this.mode = 'list';
    if (item) this.callbacks.onRename(item.path, this.renameInput.getValue().trim());
    this.rebuild();
  }

  handleInput(data: string): void {
    // 删除确认态：吞掉全部键，只响应确认/取消（pi 状态机吞键模式）
    if (this.mode === 'confirmDelete') {
      if (matchesKey(data, 'enter')) {
        const item = this.selected;
        this.mode = 'list';
        if (item) this.callbacks.onDelete(item.path);
        this.rebuild();
        return;
      }
      if (matchesKey(data, 'escape')) {
        this.exitModes();
        return;
      }
      return;
    }
    if (this.mode === 'rename') {
      this.renameInput.handleInput(data);
      return;
    }
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
    if (kb.matches(data, 'tui.select.confirm') || data === '\n') {
      const item = this.selected;
      if (item) this.callbacks.onSelect(item.path);
      return;
    }
    if (matchesKey(data, 'tab')) {
      this.scope = this.scope === 'current' ? 'all' : 'current';
      this.callbacks.onScopeChange(this.scope);
      this.rebuild();
      return;
    }
    if (matchesKey(data, 'ctrl+s')) {
      const cycle: SessionSortMode[] = ['threaded', 'recent', 'relevance'];
      const index = cycle.indexOf(this.sortMode);
      this.sortMode = cycle[(index + 1) % cycle.length];
      this.applyView();
      this.rebuild();
      return;
    }
    if (matchesKey(data, 'ctrl+n')) {
      this.namedOnly = !this.namedOnly;
      this.applyView();
      this.rebuild();
      return;
    }
    if (matchesKey(data, 'ctrl+p')) {
      this.showPath = !this.showPath;
      this.rebuild();
      return;
    }
    if (matchesKey(data, 'ctrl+d')) {
      if (this.selected) {
        this.mode = 'confirmDelete';
        this.rebuild();
      }
      return;
    }
    if (matchesKey(data, 'ctrl+r')) {
      const item = this.selected;
      if (item) {
        this.mode = 'rename';
        this.renameInput.setValue(item.name ?? '');
        this.renameInput.focused = this._focused;
        this.rebuild();
      }
      return;
    }
    // 其余输入 → 搜索框
    this.searchInput.handleInput(data);
    this.applyView();
    this.rebuild();
  }
}
