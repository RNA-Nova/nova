/**
 * 可搜索选择器基类。
 *
 * 结构：DynamicBorder + 标题 + Input 搜索框 + 过滤列表（多列元信息）
 * + 键位提示 + DynamicBorder。
 *
 * 键位分发：导航（↑↓/enter/esc）基类自处理；可打印字符 → Input →
 * fuzzyFilter 过滤重渲。具体选择器继承本类，提供 items 与（可选的）
 * 描述列/排序定制。
 */

import {
  Container,
  Input,
  Spacer,
  Text,
  getKeybindings,
  matchesKey,
  type Component,
  type Focusable,
} from '@earendil-works/pi-tui';
import { fuzzyFilter } from '@earendil-works/pi-tui';

import { DynamicBorder } from '../layout/dynamic-border.js';
import { colors } from '../../themes/index.js';
import { keyHint, rawKeyHint } from './hints.js';

/**
 * 带占位提示的 Input 包装：查询为空时渲染 dim 占位文本（不污染查询值），
 * 有内容后代工 Input 真实渲染。pi-tui Input 无 placeholder API——
 * 此前用 setValue 塞占位文本，用户每敲一个字符都拼在占位文本后（必无匹配）。
 */
class PlaceholderInput implements Component, Focusable {
  constructor(
    private readonly input: Input,
    private readonly placeholder?: string,
  ) {}

  get focused(): boolean {
    return this.input.focused;
  }
  set focused(value: boolean) {
    this.input.focused = value;
  }

  handleInput(data: string): void {
    this.input.handleInput(data);
  }

  invalidate(): void {}

  render(width: number): string[] {
    if (this.placeholder && this.input.getValue() === '') {
      return [colors.dim(` ${this.placeholder}`)];
    }
    return this.input.render(width);
  }
}

export interface SearchableItem {
  value: string;
  label: string;
  /** 描述列（元信息：时间/消息数/分组名等——dim 色显示）。 */
  description?: string;
  /** 树形层级（0 起——渲染缩进，/tree 等层级数据用）。 */
  depth?: number;
  /** 分组名（同组连续渲染，组变化处插组头行——/model 的 provider 等）。 */
  group?: string;
}

export interface SearchableOptions {
  /** 最大可见行数（超出滚动窗口跟随选中项）。 */
  maxVisible?: number;
  /** 搜索框占位提示。 */
  placeholder?: string;
}

export class SearchableSelector extends Container implements Focusable {
  protected readonly searchInput: Input;
  private readonly searchInputRow: PlaceholderInput;
  private readonly listContainer = new Container();
  private readonly titleText: Text;
  private allItems: SearchableItem[];
  private filtered: SearchableItem[];
  private selectedIndex = 0;
  private readonly maxVisible: number;
  private _focused = false;

  constructor(
    title: string,
    items: SearchableItem[],
    private readonly callbacks: {
      onSelect: (value: string) => void;
      onCancel: () => void;
      /** 高亮变化钩子（主题预览等"移动即生效"场景；初次渲染不触发）。 */
      onHighlight?: (value: string) => void;
      /** tab 键钩子（作用域切换等——/model 的 all/scoped；未提供时 tab 进搜索框）。 */
      onTab?: () => void;
    },
    options: SearchableOptions = {},
  ) {
    super();
    this.allItems = items;
    this.filtered = items;
    this.maxVisible = options.maxVisible ?? 10;

    this.addChild(new DynamicBorder());
    this.addChild(new Spacer(1));
    this.titleText = new Text(colors.accent(` ${title} `), 0, 0);
    this.addChild(this.titleText);

    this.searchInput = new Input();
    // 占位提示走包装渲染（空查询时显示），不写入输入值
    this.searchInputRow = new PlaceholderInput(this.searchInput, options.placeholder);
    this.addChild(this.searchInputRow);
    this.addChild(new Spacer(1));

    this.addChild(this.listContainer);

    this.addChild(new Spacer(1));
    this.addChild(
      new Text(
        rawKeyHint('↑↓', 'navigate') +
          '  ' +
          keyHint('tui.select.confirm', 'select') +
          '  ' +
          keyHint('tui.select.cancel', 'cancel') +
          colors.dim('  输入即过滤'),
        1,
        0,
      ),
    );
    this.addChild(new Spacer(1));
    this.addChild(new DynamicBorder());

    this.updateList();
  }

  get focused(): boolean {
    return this._focused;
  }
  set focused(value: boolean) {
    this._focused = value;
    this.searchInputRow.focused = value;
  }

  /** 当前选中项（过滤后列表）。 */
  protected get selected(): SearchableItem | undefined {
    return this.filtered[this.selectedIndex];
  }

  /** 子类可覆盖：过滤逻辑（缺省 fuzzyFilter 按 label）。 */
  protected filter(query: string): SearchableItem[] {
    const trimmed = query.trim();
    if (!trimmed) return this.allItems;
    return fuzzyFilter(this.allItems, trimmed, (item) => item.label);
  }

  /** 子类可覆盖：行渲染（缺省：→ 高亮 + depth 缩进 + label + dim 描述列）。 */
  protected renderItem(item: SearchableItem, isSelected: boolean): string {
    const prefix = isSelected ? colors.accent('→ ') : '  ';
    const indent = item.depth !== undefined && item.depth > 0 ? '  '.repeat(item.depth) : '';
    const label = isSelected ? colors.accent(item.label) : colors.text(item.label);
    const description = item.description ? colors.dim(`  ${item.description}`) : '';
    return `${prefix}${indent}${label}${description}`;
  }

  private applyFilter(): void {
    const currentValue = this.selected?.value;
    this.filtered = this.filter(this.searchInput.getValue());
    // 保持选中项（过滤后仍存在则跟随，否则回退到首项）
    const newIndex = this.filtered.findIndex((item) => item.value === currentValue);
    this.selectedIndex = newIndex >= 0 ? newIndex : 0;
    this.updateList();
    this.emitHighlight();
  }

  /** 高亮变化通知（选中项存在时）。 */
  private emitHighlight(): void {
    const selected = this.selected;
    if (selected && this.callbacks.onHighlight) this.callbacks.onHighlight(selected.value);
  }

  /** 更新标题（Tab 切作用域等状态变化——标题不再是构造期冻结文本）。 */
  setTitle(title: string): void {
    this.titleText.setText(colors.accent(` ${title} `));
  }

  /** 整体替换条目（作用域切换等——/model 的 all/scoped；选中项按 value 跟随）。 */
  setItems(items: SearchableItem[]): void {    const currentValue = this.selected?.value;
    this.allItems = items;
    this.filtered = this.filter(this.searchInput.getValue());
    const newIndex = this.filtered.findIndex((item) => item.value === currentValue);
    this.selectedIndex = newIndex >= 0 ? newIndex : 0;
    this.updateList();
  }

  private updateList(): void {
    this.listContainer.clear();
    if (this.filtered.length === 0) {
      this.listContainer.addChild(new Text(colors.warning('  无匹配'), 1, 0));
      return;
    }
    // 滚动窗口：跟随选中项
    const start = Math.max(
      0,
      Math.min(
        this.selectedIndex - Math.floor(this.maxVisible / 2),
        this.filtered.length - this.maxVisible,
      ),
    );
    const end = Math.min(start + this.maxVisible, this.filtered.length);
    let lastGroup: string | undefined;
    for (let i = start; i < end; i++) {
      const item = this.filtered[i]!;
      // 分组头：group 变化处插一行（dim 色——渲染层插入，不占数据索引）
      if (item.group !== undefined && item.group !== lastGroup) {
        this.listContainer.addChild(new Text(colors.dim(`  ${item.group}`), 1, 0));
      }
      lastGroup = item.group;
      this.listContainer.addChild(
        new Text(this.renderItem(item, i === this.selectedIndex), 1, 0),
      );
    }
    // 计数（超窗口时显示）
    if (this.filtered.length > this.maxVisible) {
      this.listContainer.addChild(
        new Text(colors.dim(`  (${this.selectedIndex + 1}/${this.filtered.length})`), 1, 0),
      );
    }
  }

  handleInput(data: string): void {
    const kb = getKeybindings();
    if (kb.matches(data, 'tui.select.up')) {
      this.selectedIndex =
        this.selectedIndex === 0 ? this.filtered.length - 1 : this.selectedIndex - 1;
      this.updateList();
      this.emitHighlight();
      return;
    }
    if (kb.matches(data, 'tui.select.down')) {
      this.selectedIndex =
        this.selectedIndex === this.filtered.length - 1 ? 0 : this.selectedIndex + 1;
      this.updateList();
      this.emitHighlight();
      return;
    }
    if (kb.matches(data, 'tui.select.confirm') || data === '\n') {
      const selected = this.selected;
      if (selected) this.callbacks.onSelect(selected.value);
      return;
    }
    if (kb.matches(data, 'tui.select.cancel')) {
      this.callbacks.onCancel();
      return;
    }
    if (matchesKey(data, 'tab') && this.callbacks.onTab) {
      this.callbacks.onTab();
      return;
    }
    // 其余输入 → 搜索框（字符/退格由 Input 自处理）
    this.searchInput.handleInput(data);
    this.applyFilter();
  }
}
