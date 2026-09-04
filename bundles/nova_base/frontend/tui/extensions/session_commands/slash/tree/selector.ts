/**
 * 会话树选择器（折叠 + 标签编辑 + 过滤 + 搜索 + 水平视口）。
 *
 * 归属：官方 bundle 的 ui/ 段（包自持命令 UI——dogfood：官方与第三方同机制）。
 * 宿主部件经 'nova-tui/modes/tui/*' 子路径导出共享（jiti 别名 + 原生 ESM
 * 缓存，主题/键位单例与宿主同实例）。
 *
 * 数据：`getSessionEntries` 全量条目 + `getSessionState.leafId`——前端自组树
 * （parentId 链接），标签从 label 条目派生（后写胜出，空标签=删除）。
 *
 * 树扁平化规则（pi TreeList 语义）：
 * - **缩进只在分支点发生**——单子链保持平级（会话树多为线性链，层层缩进会爆炸）；
 * - 隐藏/被滤条目的后代**重挂到最近可见祖先**（视觉结构不漂移）；
 * - 分支排序：含当前 leaf 的分支在最前，其余按子树最新活动倒序。
 *
 * 过滤五模式：default（隐藏元条目）/ no-tools（再隐 toolResult）/
 * user-only / labeled-only / all——ctrl+d/t/u/l/a 直切（重按回 default），
 * ctrl+o / ctrl+shift+o 前后循环。搜索：可打印字符即查询（token AND 匹配），
 * Esc 有查询先清查询（连带清折叠），过滤/查询变化清折叠（）。
 *
 * 键位：↑/↓ 环绕移动 · ← 折叠/跳父级 · → 展开/进子级 · shift+l 标签编辑 ·
 * enter 跳转 · esc 取消。折叠标记 ⊟/⊞，活跃路径 •。
 * 水平视口：仅当选中行正文起点不可见时 body 左移裁剪（光标 gutter 固定）。
 *
 * 残余挂账：ctrl+x 复制、标签时间戳切换。
 */

import {
  Container,
  Input,
  Spacer,
  Text,
  matchesKey,
  sliceByColumn,
  visibleWidth,
  type Focusable,
} from '@earendil-works/pi-tui';

import { DynamicBorder } from 'nova-tui/modes/tui/components/layout/dynamic-border';
import { colors } from 'nova-tui/modes/tui/themes/index';

/** 线上条目（camelCase 契约，自由负载——这里只消费树组装需要的字段）。 */
export interface TreeEntry {
  id: string;
  parentId: string | null;
  type: string;
  timestamp?: string;
  [key: string]: unknown;
}

/** 扁平化后的可见行。 */
export interface TreeRow {
  id: string;
  /** 连接符前缀（祖先 gutter + 本级 ├─/└─）。 */
  prefix: string;
  foldable: boolean;
  folded: boolean;
  onActivePath: boolean;
  isCurrent: boolean;
  entry: TreeEntry;
}

/** 五档过滤模式。 */
export type TreeFilterMode = 'default' | 'no-tools' | 'user-only' | 'labeled-only' | 'all';

/** 视图参数（过滤 + 搜索 + 标签表——assembleTreeRows 的注入面）。 */
export interface TreeView {
  filter: TreeFilterMode;
  query: string;
  labels: ReadonlyMap<string, string>;
}

const FILTER_CYCLE: TreeFilterMode[] = [
  'default',
  'no-tools',
  'user-only',
  'labeled-only',
  'all',
];

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function messageRole(entry: TreeEntry): string {
  return str((entry.message as { role?: unknown } | undefined)?.role);
}

/** 类型可见性（过滤模式维度——搜索匹配在其上叠加）。 */
function typeVisible(entry: TreeEntry, filter: TreeFilterMode, labels: ReadonlyMap<string, string>): boolean {
  switch (filter) {
    case 'all':
      return true;
    case 'user-only':
      return entry.type === 'message' && messageRole(entry) === 'user';
    case 'labeled-only':
      return labels.has(entry.id);
    case 'no-tools':
      if (entry.type === 'message' && messageRole(entry) === 'toolResult') return false;
      return entry.type === 'message' || entry.type === 'compaction' || entry.type === 'branch_summary';
    case 'default':
    default:
      // default：消息（全角色）+ 压缩 + 分支摘要；元条目（label/custom/
      // model_change/thinking_level_change/session_info/custom_message…）隐藏
      return entry.type === 'message' || entry.type === 'compaction' || entry.type === 'branch_summary';
  }
}

/** 从条目列表派生当前标签表（label 条目后写胜出；空标签=删除）。 */
export function deriveLabels(entries: TreeEntry[]): Map<string, string> {
  const labels = new Map<string, string>();
  for (const entry of entries) {
    if (entry.type !== 'label') continue;
    const targetId = str(entry.targetId);
    if (!targetId) continue;
    const label = str(entry.label);
    if (label) labels.set(targetId, label);
    else labels.delete(targetId);
  }
  return labels;
}

/** 标签时间戳表（与 deriveLabels 同规则取后写条目）。 */
export function deriveLabelTimestamps(entries: TreeEntry[]): Map<string, string> {
  const timestamps = new Map<string, string>();
  for (const entry of entries) {
    if (entry.type !== 'label') continue;
    const targetId = str(entry.targetId);
    if (!targetId) continue;
    const label = str(entry.label);
    if (label) timestamps.set(targetId, str(entry.timestamp));
    else timestamps.delete(targetId);
  }
  return timestamps;
}

/** ：当天 HH:MM，今年 M/D，跨年 YY/M/D。 */
export function formatLabelTimestamp(isoTimestamp: string, now = new Date()): string {
  const date = new Date(isoTimestamp);
  if (Number.isNaN(date.getTime())) return '';
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) {
    const hh = String(date.getHours()).padStart(2, '0');
    const mm = String(date.getMinutes()).padStart(2, '0');
    return `${hh}:${mm}`;
  }
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}/${date.getDate()}`;
  }
  return `${String(date.getFullYear()).slice(2)}/${date.getMonth() + 1}/${date.getDate()}`;
}

/** 条目的复制文本：消息取全文，摘要类取 summary。 */
export function entryCopyText(entry: TreeEntry): string {
  if (entry.type === 'message') {
    const message = entry.message as { content?: unknown } | undefined;
    const content = message?.content;
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return content
        .filter(
          (b): b is { type: string; text: string } =>
            typeof b === 'object' && b !== null && (b as { type?: unknown }).type === 'text',
        )
        .map((b) => b.text)
        .join('\n');
    }
    return '';
  }
  if (entry.type === 'compaction' || entry.type === 'branch_summary') {
    return str(entry.summary);
  }
  return '';
}

/** 提取消息文本首行（content 为 string 或块数组；控制字符归一为空格）。 */
function extractText(content: unknown): string {
  let text = '';
  if (typeof content === 'string') {
    text = content;
  } else if (Array.isArray(content)) {
    text = content
      .filter(
        (b): b is { type: string; text: string } =>
          typeof b === 'object' && b !== null && (b as { type?: unknown }).type === 'text',
      )
      .map((b) => b.text)
      .join(' ');
  }
  const firstLine = text.split('\n').find((line) => line.trim() !== '') ?? '';
  return firstLine.replace(/\s+/g, ' ').trim();
}

/** 条目的单行摘要（着色由调用方按行状态决定，这里只给文本）。 */
export function summarizeEntry(entry: TreeEntry, labels: ReadonlyMap<string, string>): string {
  const label = labels.get(entry.id);
  const labelPart = label ? `[${label}] ` : '';
  switch (entry.type) {
    case 'message': {
      const message = entry.message as { role?: unknown; content?: unknown } | undefined;
      const role = str(message?.role);
      const text = extractText(message?.content);
      if (role === 'user') return labelPart + (text || '[user]');
      if (role === 'assistant') return labelPart + (text || '[assistant]');
      return labelPart + (text || `[${role || 'message'}]`);
    }
    case 'compaction':
      return labelPart + '[压缩]';
    case 'branch_summary':
      return labelPart + '[分支摘要]';
    default:
      return labelPart + `[${entry.type}]`;
  }
}

/** 搜索匹配：空白切词，token AND（大小写不敏感子串）。 */
export function matchesQuery(summary: string, query: string): boolean {
  const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return true;
  const haystack = summary.toLowerCase();
  return tokens.every((token) => haystack.includes(token));
}

interface TreeNode {
  entry: TreeEntry;
  children: TreeNode[];
}

/** 组装可见行（纯函数——折叠状态与视图参数注入，便于测试）。 */
export function assembleTreeRows(
  entries: TreeEntry[],
  leafId: string | null,
  foldedIds: ReadonlySet<string>,
  view: TreeView,
): TreeRow[] {
  const byId = new Map<string, TreeNode>();
  for (const entry of entries) {
    if (!entry.id) continue;
    byId.set(entry.id, { entry, children: [] });
  }
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const parent = node.entry.parentId ? byId.get(node.entry.parentId) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }

  // 活跃路径集合（leaf 沿父链上溯）
  const activePath = new Set<string>();
  let cursor = leafId ? byId.get(leafId) : undefined;
  while (cursor) {
    activePath.add(cursor.entry.id);
    cursor = cursor.entry.parentId ? byId.get(cursor.entry.parentId) : undefined;
  }

  // 子树最新活动（ISO 时间戳字典序可比较）
  const latestCache = new Map<string, string>();
  const latestActivity = (node: TreeNode): string => {
    const cached = latestCache.get(node.entry.id);
    if (cached !== undefined) return cached;
    let latest = str(node.entry.timestamp);
    for (const child of node.children) {
      const childLatest = latestActivity(child);
      if (childLatest > latest) latest = childLatest;
    }
    latestCache.set(node.entry.id, latest);
    return latest;
  };

  // 行可见性 = 类型过滤 + 搜索匹配
  const rowVisible = (node: TreeNode): boolean =>
    typeVisible(node.entry, view.filter, view.labels) &&
    matchesQuery(summarizeEntry(node.entry, view.labels), view.query);

  // 可见子级：不可见节点的后代重挂到最近可见祖先；排序=活跃分支优先，其余按最新活动倒序
  const visibleChildren = (node: TreeNode): TreeNode[] => {
    const out: TreeNode[] = [];
    const walk = (n: TreeNode) => {
      for (const child of n.children) {
        if (rowVisible(child)) out.push(child);
        else walk(child);
      }
    };
    walk(node);
    out.sort((a, b) => {
      const aActive = activePath.has(a.entry.id) ? 1 : 0;
      const bActive = activePath.has(b.entry.id) ? 1 : 0;
      if (aActive !== bActive) return bActive - aActive;
      return latestActivity(b).localeCompare(latestActivity(a));
    });
    return out;
  };

  const visibleRoots: TreeNode[] = [];
  for (const root of roots) {
    if (rowVisible(root)) visibleRoots.push(root);
    else visibleRoots.push(...visibleChildren(root));
  }

  const rows: TreeRow[] = [];
  const visit = (
    node: TreeNode,
    gutters: string,
    connector: string,
    isLast: boolean,
    isRoot: boolean,
  ): void => {
    const folded = foldedIds.has(node.entry.id);
    rows.push({
      id: node.entry.id,
      prefix: gutters + connector,
      foldable: false, // 先占位，下方补算
      folded,
      onActivePath: activePath.has(node.entry.id),
      isCurrent: node.entry.id === leafId,
      entry: node.entry,
    });
    if (folded) return;
    const children = visibleChildren(node);
    // 规则：有可见子级 且（是根 或 父级为多子分支点）才可折叠
    rows[rows.length - 1].foldable = children.length > 0 && (isRoot || connector !== '');
    const branch = children.length > 1;
    const childGutters = gutters + (connector ? (isLast ? '   ' : '│  ') : '');
    children.forEach((child, index) => {
      const last = index === children.length - 1;
      visit(child, childGutters, branch ? (last ? '└─ ' : '├─ ') : '', last, false);
    });
  };
  for (const root of visibleRoots) {
    visit(root, '', '', true, true);
  }
  return rows;
}

export interface TreeSelectorCallbacks {
  onSelect: (entryId: string) => void;
  onCancel: () => void;
  /** 标签编辑（label 为 undefined 表示清除）。组件乐观更新本地标签表。 */
  onLabelEdit: (entryId: string, label: string | undefined) => void;
  /** 复制选中条目全文（ctrl+x——控制器实现剪贴板写入）。 */
  onCopy?: (entryId: string) => void;
}

export class TreeSelector extends Container implements Focusable {
  private _focused = false;
  private readonly foldedIds = new Set<string>();
  private labels: Map<string, string>;
  private readonly labelTimestamps: Map<string, string>;
  private showLabelTimestamp = false;
  private filterMode: TreeFilterMode = 'default';
  private searchQuery = '';
  private rows: TreeRow[];
  private selectedIndex = 0;
  private mode: 'tree' | 'label' = 'tree';
  private readonly body = new Container();
  private readonly labelInput = new Input();
  private readonly maxVisible = 10;

  constructor(
    private readonly entries: TreeEntry[],
    private readonly leafId: string | null,
    labels: Map<string, string>,
    private readonly callbacks: TreeSelectorCallbacks,
    labelTimestamps?: Map<string, string>,
    initialFilter?: TreeFilterMode,
  ) {
    super();
    this.labels = new Map(labels);
    this.labelTimestamps = new Map(labelTimestamps ?? []);
    this.filterMode = initialFilter ?? 'default';
    this.rows = this.assemble();
    // 初始选中当前 leaf（找不到则第一行）
    const leafIndex = this.rows.findIndex((row) => row.isCurrent);
    if (leafIndex >= 0) this.selectedIndex = leafIndex;

    this.addChild(new DynamicBorder());
    this.addChild(new Text(colors.accent(' 会话树 '), 0, 0));
    this.addChild(this.body);
    this.addChild(new DynamicBorder());

    this.labelInput.onSubmit = () => this.commitLabel();
    this.labelInput.onEscape = () => this.exitLabelMode();
    this.rebuild();
  }

  get focused(): boolean {
    return this._focused;
  }
  set focused(value: boolean) {
    this._focused = value;
    this.labelInput.focused = value && this.mode === 'label';
  }

  private get selected(): TreeRow | undefined {
    return this.rows[this.selectedIndex];
  }

  private get view(): TreeView {
    return { filter: this.filterMode, query: this.searchQuery, labels: this.labels };
  }

  /** 展示用标签表（shift+t 开启时拼上时间戳；过滤/搜索始终用原始表）。 */
  private get displayLabels(): Map<string, string> {
    if (!this.showLabelTimestamp) return this.labels;
    return new Map(
      [...this.labels].map(([key, value]) => {
        const ts = this.labelTimestamps.get(key);
        const formatted = ts ? formatLabelTimestamp(ts) : '';
        return [key, formatted ? `${value} ${formatted}` : value];
      }),
    );
  }

  private assemble(): TreeRow[] {
    return assembleTreeRows(this.entries, this.leafId, this.foldedIds, this.view);
  }

  /** 过滤/查询变化：清折叠（）+ 重组装 + 选中钳位。 */
  private onViewChange(): void {
    this.foldedIds.clear();
    this.rows = this.assemble();
    this.selectedIndex = Math.max(0, Math.min(this.selectedIndex, this.rows.length - 1));
  }

  /** 状态变化后重建可见区（折叠/选择移动/标签/过滤/搜索共用）。 */
  private rebuild(): void {
    this.rows = this.assemble();
    this.selectedIndex = Math.max(0, Math.min(this.selectedIndex, this.rows.length - 1));
    this.body.clear();
    if (this.mode === 'label') {
      this.body.addChild(new Text(colors.dim(' 标签（留空删除）：'), 0, 0));
      this.body.addChild(this.labelInput);
      this.body.addChild(new Spacer(1));
      this.body.addChild(new Text(colors.dim(' enter 保存 · esc 返回'), 0, 0));
      return;
    }
    this.body.addChild(
      new Text(
        colors.dim(
          ` ↑↓ 移动 · ←→ 折叠 · shift+l 标签 · shift+t 时间戳 · ctrl+x 复制 · enter 跳转 · ctrl+d/t/u/l/a 过滤(${this.filterMode}) · esc 取消`,
        ),
        0,
        0,
      ),
    );
    if (this.searchQuery) {
      this.body.addChild(new Text(colors.dim(` 搜索: ${this.searchQuery}`), 0, 0));
    }
    this.body.addChild(new Spacer(1));
    if (this.rows.length === 0) {
      this.body.addChild(new Text(colors.dim(' （无匹配行）'), 0, 0));
      return;
    }
    // 滚动窗口跟随选中项（居中钳位）
    const half = Math.floor(this.maxVisible / 2);
    const start = Math.max(
      0,
      Math.min(this.selectedIndex - half, this.rows.length - this.maxVisible),
    );
    const windowRows = this.rows.slice(start, start + this.maxVisible);
    // 水平视口：仅当选中行正文起点
    // 不可见时 body 左移；2 列光标 gutter 固定不动
    const bodies = windowRows.map((row, offset) => {
      const isSelected = start + offset === this.selectedIndex;
      const foldMark = row.foldable ? (row.folded ? '⊞ ' : '⊟ ') : '';
      const activeMark = row.onActivePath ? '• ' : '  ';
      return {
        isSelected,
        body: `${row.prefix}${foldMark}${activeMark}${summarizeEntry(row.entry, this.displayLabels)}`,
        anchor: visibleWidth(`${row.prefix}${foldMark}${activeMark}`),
      };
    });
    this.body.addChild(new TreeRowsView(bodies, this.selectedIndex - start));
    this.body.addChild(new Spacer(1));
    this.body.addChild(
      new Text(colors.dim(` (${this.selectedIndex + 1}/${this.rows.length})`), 0, 0),
    );
  }

  private move(delta: number): void {
    if (this.rows.length === 0) return;
    // 环绕移动（pi tree 语义）
    this.selectedIndex = (this.selectedIndex + delta + this.rows.length) % this.rows.length;
    this.rebuild();
  }

  /** ←：折叠当前节点；不可折叠/已折叠则跳到父级行。 */
  private foldOrUp(): void {
    const row = this.selected;
    if (!row) return;
    if (row.foldable && !row.folded) {
      this.foldedIds.add(row.id);
      this.rebuild();
      return;
    }
    // 父级可能不可见——沿父链找最近可见行
    let cursor: TreeEntry | undefined = row.entry;
    const byId = new Map(this.entries.map((entry) => [entry.id, entry]));
    while (cursor?.parentId) {
      cursor = byId.get(cursor.parentId);
      const index = this.rows.findIndex((r) => r.id === cursor?.id);
      if (index >= 0) {
        this.selectedIndex = index;
        this.rebuild();
        return;
      }
    }
  }

  /** →：展开已折叠节点；未折叠且有可见子级则进第一个子级行。 */
  private unfoldOrDown(): void {
    const row = this.selected;
    if (!row) return;
    if (row.folded) {
      this.foldedIds.delete(row.id);
      this.rebuild();
      return;
    }
    const childIndex = this.rows.findIndex(
      (r, index) => index > this.selectedIndex && r.entry.parentId === row.id,
    );
    if (childIndex >= 0) {
      this.selectedIndex = childIndex;
      this.rebuild();
    }
  }

  /** 过滤模式直切（重按当前模式回 default）。 */
  private setFilter(mode: TreeFilterMode): void {
    this.filterMode = this.filterMode === mode && mode !== 'default' ? 'default' : mode;
    this.onViewChange();
    this.rebuild();
  }

  /** 过滤循环（ctrl+o 前 / ctrl+shift+o 后）。 */
  private cycleFilter(direction: 1 | -1): void {
    const index = FILTER_CYCLE.indexOf(this.filterMode);
    this.filterMode =
      FILTER_CYCLE[(index + direction + FILTER_CYCLE.length) % FILTER_CYCLE.length];
    this.onViewChange();
    this.rebuild();
  }

  private enterLabelMode(): void {
    const row = this.selected;
    if (!row) return;
    this.mode = 'label';
    this.labelInput.setValue(this.labels.get(row.id) ?? '');
    this.labelInput.focused = this._focused;
    this.rebuild();
  }

  private exitLabelMode(): void {
    this.mode = 'tree';
    this.rebuild();
  }

  private commitLabel(): void {
    const row = this.selected;
    this.mode = 'tree';
    if (!row) {
      this.rebuild();
      return;
    }
    const text = this.labelInput.getValue().trim();
    const label = text === '' ? undefined : text;
    // 乐观更新本地标签表与时间戳（RPC 由控制器异步发出）
    if (label === undefined) {
      this.labels.delete(row.id);
      this.labelTimestamps.delete(row.id);
    } else {
      this.labels.set(row.id, label);
      this.labelTimestamps.set(row.id, new Date().toISOString());
    }
    this.callbacks.onLabelEdit(row.id, label);
    this.rebuild();
  }

  handleInput(data: string): void {
    if (this.mode === 'label') {
      // Input 自己的 onSubmit/onEscape 已接；其余键位委托
      this.labelInput.handleInput(data);
      return;
    }
    if (matchesKey(data, 'escape')) {
      // 有搜索词：先清搜索 + 清折叠（）；否则取消
      if (this.searchQuery) {
        this.searchQuery = '';
        this.onViewChange();
        this.rebuild();
        return;
      }
      this.callbacks.onCancel();
      return;
    }
    if (matchesKey(data, 'enter')) {
      const row = this.selected;
      if (row) this.callbacks.onSelect(row.id);
      return;
    }
    if (matchesKey(data, 'up')) {
      this.move(-1);
      return;
    }
    if (matchesKey(data, 'down')) {
      this.move(1);
      return;
    }
    if (matchesKey(data, 'left')) {
      this.foldOrUp();
      return;
    }
    if (matchesKey(data, 'right')) {
      this.unfoldOrDown();
      return;
    }
    if (matchesKey(data, 'ctrl+o')) {
      this.cycleFilter(1);
      return;
    }
    if (matchesKey(data, 'ctrl+shift+o')) {
      this.cycleFilter(-1);
      return;
    }
    if (matchesKey(data, 'ctrl+d')) {
      this.setFilter('default');
      return;
    }
    if (matchesKey(data, 'ctrl+t')) {
      this.setFilter('no-tools');
      return;
    }
    if (matchesKey(data, 'ctrl+u')) {
      this.setFilter('user-only');
      return;
    }
    if (matchesKey(data, 'ctrl+l')) {
      this.setFilter('labeled-only');
      return;
    }
    if (matchesKey(data, 'ctrl+a')) {
      this.setFilter('all');
      return;
    }
    if (data === 'L') {
      this.enterLabelMode();
      return;
    }
    if (data === 'T') {
      // shift+t：标签时间戳显隐
      this.showLabelTimestamp = !this.showLabelTimestamp;
      this.rebuild();
      return;
    }
    if (matchesKey(data, 'ctrl+x')) {
      // 复制选中条目全文（—控制器写剪贴板）
      const row = this.selected;
      if (row) this.callbacks.onCopy?.(row.id);
      return;
    }
    // 退格：删搜索词尾字符
    if (matchesKey(data, 'backspace')) {
      if (this.searchQuery) {
        this.searchQuery = this.searchQuery.slice(0, -1);
        this.onViewChange();
        this.rebuild();
      }
      return;
    }
    // 可打印字符累积进搜索词（无独立输入框）
    if (data.length === 1 && data >= ' ') {
      this.searchQuery += data;
      this.onViewChange();
      this.rebuild();
    }
  }
}

/** 行列表视图（水平视口的渲染点——宽度在渲染期才确定，故独立组件）。 */
class TreeRowsView extends Container {
  constructor(
    private readonly bodies: Array<{ isSelected: boolean; body: string; anchor: number }>,
    private readonly selectedWindowIndex: number,
  ) {
    super();
  }

  override render(width: number): string[] {
    const gutter = 2; // '› ' 光标列固定
    const viewportBody = Math.max(8, width - gutter);
    const selected = this.bodies[this.selectedWindowIndex];
    const maxBody = Math.max(...this.bodies.map((r) => visibleWidth(r.body)), 0);
    // 仅当选中行正文起点不可见时左移；平移量钳制在 maxBodyWidth − viewport
    let shift = 0;
    if (selected && selected.anchor >= viewportBody) {
      shift = Math.min(selected.anchor - viewportBody + 4, Math.max(0, maxBody - viewportBody));
    }
    return this.bodies.map((row) => {
      const cursorMark = row.isSelected ? '› ' : '  ';
      const body = sliceByColumn(row.body, shift, shift + viewportBody);
      const line = cursorMark + body;
      return row.isSelected ? colors.accent(line) : line;
    });
  }
}
