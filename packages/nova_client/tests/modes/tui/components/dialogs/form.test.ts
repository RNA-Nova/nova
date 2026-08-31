/**
 * FormDialog 键位路由与提交/取消语义测试。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { FormDialog, type FormFieldSpec } from '../../../../../src/modes/tui/components/dialogs/form.js';

const FIELDS: FormFieldSpec[] = [
  { key: 'name', label: '名称', placeholder: 'nova' },
  { key: 'desc', label: '描述' },
  { key: 'tags', label: '标签' },
];

function makeDialog(handlers: {
  onSubmit?: (values: Record<string, string>) => void;
  onCancel?: () => void;
}): FormDialog {
  const dialog = new FormDialog('建资源', FIELDS, {
    onSubmit: handlers.onSubmit ?? (() => {}),
    onCancel: handlers.onCancel ?? (() => {}),
  });
  // 真实使用中由 TUI setFocus 触发 focused setter（光标归位逻辑挂在那里）
  dialog.focused = true;
  return dialog;
}

// 键位的终端转义序列（matchesKey 识别的原始输入）
const KEYS = {
  enter: '\r',
  escape: '\x1b',
  tab: '\t',
  shiftTab: '\x1b[Z',
  down: '\x1b[B',
  up: '\x1b[A',
  ctrlEnter: '\x1b[13;5u',
} as const;

describe('FormDialog', () => {
  it('初始渲染：首字段高亮（› 前缀），placeholder 作预填值', () => {
    const out = makeDialog({}).render(80).join('\n');
    assert.match(out, /› 名称/);
    assert.match(out, /nova/); // 预填值
    assert.doesNotMatch(out, /› 描述/);
  });

  it('tab/down 移动活跃字段，端点钳位不环绕', () => {
    const dialog = makeDialog({});
    dialog.handleInput(KEYS.tab);
    let out = dialog.render(80).join('\n');
    assert.match(out, /› 描述/);
    dialog.handleInput(KEYS.down);
    dialog.handleInput(KEYS.down); // 越过末字段 → 钳位在末字段
    out = dialog.render(80).join('\n');
    assert.match(out, /› 标签/);
    dialog.handleInput(KEYS.up);
    out = dialog.render(80).join('\n');
    assert.match(out, /› 描述/);
    dialog.handleInput(KEYS.shiftTab);
    dialog.handleInput(KEYS.shiftTab); // 越过首字段 → 钳位
    out = dialog.render(80).join('\n');
    assert.match(out, /› 名称/);
  });

  it('enter 在末字段提交；其余位置移到下一字段', () => {
    let submitted: Record<string, string> | undefined;
    const dialog = makeDialog({ onSubmit: (v) => (submitted = v) });
    dialog.handleInput(KEYS.enter); // name → desc
    assert.equal(submitted, undefined);
    dialog.handleInput(KEYS.enter); // desc → tags
    assert.equal(submitted, undefined);
    dialog.handleInput(KEYS.enter); // tags → 提交
    assert.deepEqual(submitted, { name: 'nova', desc: '', tags: '' });
  });

  it('ctrl+enter 任意位置提交，返回全部字段值', () => {
    let submitted: Record<string, string> | undefined;
    const dialog = makeDialog({ onSubmit: (v) => (submitted = v) });
    dialog.handleInput('x'); // 键入进活跃字段（name，预填 nova 后追加）
    dialog.handleInput(KEYS.ctrlEnter);
    assert.deepEqual(submitted, { name: 'novax', desc: '', tags: '' });
  });

  it('esc 取消', () => {
    let cancelled = false;
    const dialog = makeDialog({ onCancel: () => (cancelled = true) });
    dialog.handleInput(KEYS.escape);
    assert.equal(cancelled, true);
  });
});
