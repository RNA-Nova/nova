/**
 * 助手/用户消息视图的 OSC 133 区段标记测试（—首行 A、末行 B+C，空渲染不注入）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { AssistantView } from '../../../../../src/modes/tui/components/transcript/assistant-message.js';
import { UserMessageView } from '../../../../../src/modes/tui/components/transcript/user-message.js';

const A = '\x1b]133;A\x07';
const BC = '\x1b]133;B\x07\x1b]133;C\x07';

describe('OSC 133 区段标记', () => {
  it('AssistantView：首行 A、末行 B+C', () => {
    const view = new AssistantView('你好\n世界');
    const lines = view.render(80);
    assert.ok(lines.length > 0);
    assert.ok(lines[0]!.startsWith(A), '首行应以 OSC 133;A 开头');
    assert.ok(lines[lines.length - 1]!.startsWith(BC), '末行应以 OSC 133;B+C 开头');
  });

  it('AssistantView：空内容（无 text/thinking/stopReason）渲染为空时不注入', () => {
    const view = new AssistantView('');
    // Spacer 先行——contentContainer 空时整体无行
    const lines = view.render(80);
    assert.equal(lines.some((line) => line.includes('\x1b]133;')), false);
  });

  it('UserMessageView：首行 A、末行 B+C（既有行为回归）', () => {
    const view = new UserMessageView('用户输入', { expanded: false });
    const lines = view.render(80);
    assert.ok(lines[0]!.startsWith(A));
    assert.ok(lines[lines.length - 1]!.startsWith(BC));
  });

  it('AssistantView：带 thinking 时仍仅首尾注入一次', () => {
    const view = new AssistantView('正文', '思考内容');
    const lines = view.render(80);
    const marked = lines.filter((line) => line.includes('\x1b]133;'));
    assert.equal(marked.length, 2); // 首行 + 末行各一
    assert.ok(marked[0]!.startsWith(A));
    assert.ok(marked[1]!.startsWith(BC));
  });
});
