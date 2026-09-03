/**
 * 多字段表单对话框（form 原语的前端组件）。
 *
 * 标准词汇的第五件（复合原语——四件套之外的官方扩展，定义权在
 * nova_coding_agent/ui_primitives.py）。字段纵向堆叠：标签行 + 输入行；
 * 活跃字段高亮（› 前缀 + accent 色），键位：
 * - tab / ↓：下一字段；shift+tab / ↑：上一字段（端点钳位，不环绕）；
 * - enter：下一字段，末字段提交；ctrl+enter：任意位置提交；
 * - esc：取消。
 *
 * 生命周期与四件套一致：dialogs 控制器替换编辑器槽位、应答后恢复。
 */

import chalk from 'chalk';
import {
  Box,
  Container,
  Input,
  Spacer,
  Text,
  matchesKey,
  type Focusable,
} from '@earendil-works/pi-tui';

/** 表单字段规格（线上词汇键：key/label/placeholder——placeholder 语义为预填值）。 */
export interface FormFieldSpec {
  key: string;
  label: string;
  placeholder?: string;
}

export interface FormDialogOptions {
  onSubmit: (values: Record<string, string>) => void;
  onCancel: () => void;
}

export class FormDialog extends Container implements Focusable {
  private _focused = false;
  private readonly fields: FormFieldSpec[];
  private readonly inputs: Input[] = [];
  private readonly labels: Text[] = [];
  private readonly cursorInitialized = new Set<number>();
  private activeIndex = 0;

  constructor(
    title: string,
    fields: FormFieldSpec[],
    private readonly options: FormDialogOptions,
  ) {
    super();
    this.fields = fields;
    this.addChild(new Box(1, 0, (s) => chalk.bgCyan.black(s)));
    this.addChild(new Text(` ${title} `, 0, 0));
    this.addChild(new Spacer(1));
    for (const field of fields) {
      const label = new Text('', 0, 0);
      this.labels.push(label);
      this.addChild(label);
      const input = new Input();
      // placeholder 语义与 input 原语对齐：作为预填值
      if (field.placeholder) input.setValue(field.placeholder);
      this.inputs.push(input);
      this.addChild(input);
      this.addChild(new Spacer(1));
    }
    this.addChild(
      new Text(chalk.dim(' tab/↑↓ 切换字段 · enter 下一项 · ctrl+enter 提交 · esc 取消'), 1, 0),
    );
    this.refreshLabels();
  }

  get focused(): boolean {
    return this._focused;
  }
  set focused(value: boolean) {
    this._focused = value;
    this.syncInputFocus();
  }

  private move(delta: number): void {
    const next = this.activeIndex + delta;
    // 端点钳位（对齐 pi extension-selector 的不环绕语义）
    this.activeIndex = Math.max(0, Math.min(this.fields.length - 1, next));
    this.refreshLabels();
    this.syncInputFocus();
  }

  private submit(): void {
    const values: Record<string, string> = {};
    for (let i = 0; i < this.fields.length; i++) {
      values[this.fields[i].key] = this.inputs[i].getValue();
    }
    this.options.onSubmit(values);
  }

  /** 活跃字段标签高亮（› 前缀 + accent），其余 dim。 */
  private refreshLabels(): void {
    for (let i = 0; i < this.fields.length; i++) {
      const text = i === this.activeIndex ? ` › ${this.fields[i].label} ` : `   ${this.fields[i].label} `;
      this.labels[i].setText(i === this.activeIndex ? chalk.cyan(text) : chalk.gray(text));
    }
  }

  /** Input 的内部光标渲染跟随活跃字段；首次激活时光标移到预填值末尾。 */
  private syncInputFocus(): void {
    for (let i = 0; i < this.inputs.length; i++) {
      const active = this._focused && i === this.activeIndex;
      this.inputs[i].focused = active;
      if (active && !this.cursorInitialized.has(i)) {
        this.cursorInitialized.add(i);
        // 预填值的初始光标移到末尾（end 键序列；之后切换保留用户光标位置）
        this.inputs[i].handleInput('\x1b[F');
      }
    }
  }

  handleInput(data: string): void {
    if (matchesKey(data, 'escape')) {
      this.options.onCancel();
      return;
    }
    if (matchesKey(data, 'ctrl+enter')) {
      this.submit();
      return;
    }
    if (matchesKey(data, 'tab') || matchesKey(data, 'down')) {
      this.move(1);
      return;
    }
    if (matchesKey(data, 'shift+tab') || matchesKey(data, 'up')) {
      this.move(-1);
      return;
    }
    if (matchesKey(data, 'enter')) {
      if (this.activeIndex === this.fields.length - 1) this.submit();
      else this.move(1);
      return;
    }
    // 其余键位委托给活跃字段的 Input（单字段内编辑语义不变）
    this.inputs[this.activeIndex]?.handleInput(data);
  }
}
