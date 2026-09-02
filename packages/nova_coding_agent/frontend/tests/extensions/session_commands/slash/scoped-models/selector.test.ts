/**
 * ScopedModelsSelector 测试（bundle frontend/tui/extensions/session_commands/slash/scoped-models/selector.ts——
 * 自 nova-client 宿主迁入）：buildScopedRows 初始组装 + 面板状态机
 * （切换启用/排序/全启/全清/dirty 标记/保存负载）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  buildScopedRows,
  ScopedModelsSelector,
} from '../../../../../tui/extensions/session_commands/slash/scoped-models/selector.js';

const KEYS = {
  enter: '\r',
  escape: '\x1b',
  space: ' ',
  ctrlA: '\x01',
  ctrlS: '\x13',
  ctrlX: '\x18',
  altUp: '\x1bp',
  altDown: '\x1bn',
} as const;

function scopedEntry(provider: string, id: string, thinkingLevel: string | null = null) {
  return { provider, id, thinkingLevel };
}

function modelEntry(provider: string, id: string, name = id) {
  return { provider, id, name };
}

const ALL = [
  modelEntry('openai', 'gpt-4o', 'GPT-4o'),
  modelEntry('volcengine', 'deepseek', 'DeepSeek'),
  modelEntry('volcengine', 'doubao', 'Doubao'),
];

function makeSelector(
  scoped: Array<{ provider: string; id: string; thinkingLevel: string | null }> = [],
  all = ALL,
) {
  const calls = { save: [] as string[][], cancel: 0 };
  const selector = new ScopedModelsSelector(scoped, all, {
    onSave: (keys) => calls.save.push(keys),
    onCancel: () => calls.cancel++,
  });
  selector.focused = true;
  return { selector, calls };
}

describe('buildScopedRows', () => {
  it('scoped 序在前（保循环顺序 + thinkingLevel），未启用按 all 序在后', () => {
    const { enabled, disabled } = buildScopedRows(
      [scopedEntry('volcengine', 'deepseek', 'high'), scopedEntry('openai', 'gpt-4o')],
      ALL,
    );
    assert.deepEqual(
      enabled.map((row) => [row.key, row.thinkingLevel]),
      [
        ['volcengine/deepseek', 'high'],
        ['openai/gpt-4o', null],
      ],
    );
    assert.deepEqual(
      disabled.map((row) => row.key),
      ['volcengine/doubao'],
    );
  });

  it('scoped 条目不在 all 清单时 name 回退为 id', () => {
    const { enabled } = buildScopedRows([scopedEntry('ghost', 'm1')], ALL);
    assert.equal(enabled[0].name, 'm1');
  });
});

describe('ScopedModelsSelector 状态机', () => {
  it('space 切换启用：启用追加到循环序末尾，禁用移出', () => {
    const { selector, calls } = makeSelector([scopedEntry('openai', 'gpt-4o')]);
    // 选中首行（已启用的 gpt-4o）——space 禁用（行序重排：gpt-4o 落到未启用段末尾）
    selector.handleInput(KEYS.space);
    selector.handleInput(KEYS.ctrlS);
    assert.deepEqual(calls.save, [[]]);
    // 移到未启用段第二行（doubao）——space 启用（追加到循环序末尾）
    selector.handleInput('\x1b[B'); // ↓
    selector.handleInput(KEYS.space);
    selector.handleInput(KEYS.ctrlS);
    assert.deepEqual(calls.save[1], ['volcengine/doubao']);
  });

  it('alt+↑/↓ 调整启用项循环顺序（未启用行不可移动）', () => {
    const { selector, calls } = makeSelector([
      scopedEntry('openai', 'gpt-4o'),
      scopedEntry('volcengine', 'deepseek'),
    ]);
    // 首行（gpt-4o）下移一位
    selector.handleInput(KEYS.altDown);
    selector.handleInput(KEYS.ctrlS);
    assert.deepEqual(calls.save[0], ['volcengine/deepseek', 'openai/gpt-4o']);
    // 回到首位后 alt+↑ 越界不动
    selector.handleInput(KEYS.altUp);
    selector.handleInput(KEYS.altUp);
    selector.handleInput(KEYS.ctrlS);
    assert.deepEqual(calls.save[1], ['volcengine/deepseek', 'openai/gpt-4o']);
  });

  it('ctrl+a 全启用（无搜索词——追加到循环序末尾）；ctrl+x 全清', () => {
    const { selector, calls } = makeSelector([scopedEntry('openai', 'gpt-4o')]);
    selector.handleInput(KEYS.ctrlA);
    selector.handleInput(KEYS.ctrlS);
    assert.deepEqual(calls.save[0], ['openai/gpt-4o', 'volcengine/deepseek', 'volcengine/doubao']);
    selector.handleInput(KEYS.ctrlX);
    selector.handleInput(KEYS.ctrlS);
    assert.deepEqual(calls.save[1], []);
  });

  it('有搜索词时 ctrl+a 仅启用过滤结果', () => {
    const { selector, calls } = makeSelector([]);
    for (const ch of 'deepseek') selector.handleInput(ch);
    selector.handleInput(KEYS.ctrlA);
    selector.handleInput(KEYS.ctrlS);
    assert.deepEqual(calls.save[0], ['volcengine/deepseek']);
  });

  it('未保存改动标题带 (unsaved)，保存负载即循环顺序', () => {
    const { selector } = makeSelector([scopedEntry('openai', 'gpt-4o')]);
    assert.doesNotMatch(selector.render(100).join('\n'), /unsaved/);
    selector.handleInput(KEYS.ctrlA); // 改动 → dirty
    assert.match(selector.render(100).join('\n'), /unsaved/);
  });

  it('esc 取消（不写保存负载）', () => {
    const { selector, calls } = makeSelector([scopedEntry('openai', 'gpt-4o')]);
    selector.handleInput(KEYS.ctrlA);
    selector.handleInput(KEYS.escape);
    assert.equal(calls.cancel, 1);
    assert.equal(calls.save.length, 0);
  });
});
