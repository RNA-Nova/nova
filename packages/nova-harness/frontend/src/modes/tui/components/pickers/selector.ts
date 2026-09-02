/**
 * 选择器基础件（pi ExtensionSelectorComponent 对位，SelectList 结构化版）。
 *
 * 视觉：DynamicBorder 上下框 + accent 标题 + SelectList（滚动/高亮/计数）
 * + 键位提示行；可选 CountdownTimer（对话框 timeout 的前端呈现）。
 *
 * 所有具体选择器（session/model/user-message/...）基于此件或
 * searchable.ts（可搜索基类）。
 */

import {
  Container,
  SelectList,
  Spacer,
  Text,
  TUI,
  type Component,
  type Focusable,
  type SelectListTheme,
} from '@earendil-works/pi-tui';

import { CountdownTimer } from '../status/countdown-timer.js';
import { DynamicBorder } from '../layout/dynamic-border.js';
import { colors } from '../../themes/index.js';
import { keyHint, rawKeyHint } from './hints.js';

export interface SelectorItem {
  value: string;
  label: string;
  description?: string;
}

export interface SelectorOptions {
  /** 对话框 timeout（毫秒）——开启倒计时，到期按取消处理。 */
  timeoutMs?: number;
  /** 最大可见行数（超出滚动）。 */
  maxVisible?: number;
  /** 底部提示行（缺省为 navigate/select/cancel 三件套）。 */
  hints?: string;
}

const selectListTheme: SelectListTheme = {
  selectedPrefix: (s) => colors.accent(s),
  selectedText: (s) => colors.accent(s),
  description: (s) => colors.muted(s),
  scrollInfo: (s) => colors.dim(s),
  noMatch: (s) => colors.warning(s),
};

export class Selector extends Container implements Focusable {
  private readonly list: SelectList;
  private readonly countdown: CountdownTimer | undefined;
  private _focused = false;

  constructor(
    tui: TUI,
    title: string,
    items: SelectorItem[],
    private readonly callbacks: {
      onSelect: (value: string) => void;
      onCancel: () => void;
    },
    options: SelectorOptions = {},
  ) {
    super();

    this.addChild(new DynamicBorder());
    this.addChild(new Spacer(1));

    const titleText = new Text(colors.accent(` ${title} `), 0, 0);
    this.addChild(titleText);

    if (options.timeoutMs && options.timeoutMs > 0) {
      this.countdown = new CountdownTimer(
        options.timeoutMs,
        tui,
        (seconds) => titleText.setText(colors.accent(` ${title} (${seconds}s) `)),
        () => this.callbacks.onCancel(),
      );
    }

    this.list = new SelectList(
      items.map((item) => ({
        value: item.value,
        label: item.label,
        description: item.description,
      })),
      Math.min(options.maxVisible ?? 10, items.length || 1),
      selectListTheme,
    );
    this.list.onSelect = (item) => this.callbacks.onSelect(item.value);
    this.list.onCancel = () => this.callbacks.onCancel();
    this.addChild(this.list);

    this.addChild(new Spacer(1));
    this.addChild(
      new Text(
        options.hints ??
          rawKeyHint('↑↓', 'navigate') +
            '  ' +
            keyHint('tui.select.confirm', 'select') +
            '  ' +
            keyHint('tui.select.cancel', 'cancel'),
        1,
        0,
      ),
    );
    this.addChild(new Spacer(1));
    this.addChild(new DynamicBorder());
  }

  get focused(): boolean {
    return this._focused;
  }
  set focused(value: boolean) {
    this._focused = value;
  }

  handleInput(data: string): void {
    this.list.handleInput(data);
  }

  dispose(): void {
    this.countdown?.dispose();
  }
}
