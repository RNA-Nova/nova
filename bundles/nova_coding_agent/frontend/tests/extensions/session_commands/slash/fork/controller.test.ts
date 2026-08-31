/**
 * /fork 编排纯函数测试（frontend/tui/extensions/session_commands/slash/fork/controller.ts）：
 * extractUserText——字符串/块数组/空白归一；buildForkItems——user 过滤、
 * 最新在前、倒数位次描述、空消息占位。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  buildForkItems,
  extractUserText,
} from '../../../../../tui/extensions/session_commands/slash/fork/controller.js';

function userEntry(id: string, content: unknown) {
  return { id, type: 'message', message: { role: 'user', content } };
}

describe('extractUserText', () => {
  it('字符串 content 原样（空白归一）', () => {
    assert.equal(extractUserText('你好\n世界  ！'), '你好 世界 ！');
  });

  it('块数组：仅取 text 块拼接', () => {
    const content = [
      { type: 'text', text: '第一段' },
      { type: 'image', data: '...' },
      { type: 'text', text: '第二段' },
    ];
    assert.equal(extractUserText(content), '第一段 第二段');
  });

  it('非文本输入：空串', () => {
    assert.equal(extractUserText(undefined), '');
    assert.equal(extractUserText(null), '');
    assert.equal(extractUserText(42), '');
  });
});

describe('buildForkItems', () => {
  it('只收 user 消息（assistant/toolResult 与非 message 条目排除）', () => {
    const items = buildForkItems([
      userEntry('u1', '问题一'),
      { id: 'a1', type: 'message', message: { role: 'assistant', content: '回答' } },
      { id: 't1', type: 'message', message: { role: 'toolResult', content: 'ok' } },
      { id: 'c1', type: 'compaction' },
      userEntry('u2', '问题二'),
    ]);
    assert.deepEqual(
      items.map((item) => item.value),
      ['u2', 'u1'],
    );
  });

  it('最新在前（时间序反转），description 为倒数位次', () => {
    const items = buildForkItems([
      userEntry('u1', '第一条'),
      userEntry('u2', '第二条'),
      userEntry('u3', '第三条'),
    ]);
    assert.deepEqual(
      items.map((item) => [item.value, item.description]),
      [
        ['u3', '消息 3/3'],
        ['u2', '消息 2/3'],
        ['u1', '消息 1/3'],
      ],
    );
  });

  it('空文本消息占位 (空消息)；缺 id 条目排除', () => {
    const items = buildForkItems([
      userEntry('u1', '   '),
      { type: 'message', message: { role: 'user', content: '无 id' } },
    ]);
    assert.equal(items.length, 1);
    assert.equal(items[0].label, '(空消息)');
  });
});
