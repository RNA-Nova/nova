/**
 * /todos 编排纯函数测试（frontend/tui/extensions/session_commands/slash/todos/controller.ts）：
 * 分支回溯（leaf→root）、最新清单胜出、旁支隔离、空历史语义。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { latestTodosFromEntries } from '../../../../../tui/extensions/session_commands/slash/todos/controller.js';

function msg(id: string, parentId: string | null, role = 'user') {
  return { id, parentId, type: 'message', message: { role, content: [] } };
}

function todoResult(id: string, parentId: string | null, todos: Array<{ content: string; status: string }>) {
  return {
    id,
    parentId,
    type: 'message',
    message: { role: 'toolResult', toolName: 'todo', details: { todos } },
  };
}

describe('latestTodosFromEntries', () => {
  it('从未有 todo 结果时返回 undefined', () => {
    const entries = [msg('a', null), msg('b', 'a', 'assistant')];
    assert.equal(latestTodosFromEntries(entries, 'b'), undefined);
  });

  it('取当前分支最新一条 todo 清单（后写胜出）', () => {
    const entries = [
      msg('a', null),
      todoResult('t1', 'a', [{ content: 'old', status: 'pending' }]),
      msg('b', 't1', 'assistant'),
      todoResult('t2', 'b', [{ content: 'new', status: 'completed' }]),
      msg('c', 't2', 'assistant'),
    ];
    assert.deepEqual(latestTodosFromEntries(entries, 'c'), [
      { content: 'new', status: 'completed' },
    ]);
  });

  it('树导航回历史点时看到该点的快照（旁支隔离）', () => {
    const entries = [
      msg('a', null),
      todoResult('t1', 'a', [{ content: 'main-branch', status: 'pending' }]),
      msg('b', 't1'),
      // 旁支：从 a 分出的另一个分支上有更新的 todo
      todoResult('t2', 'a', [{ content: 'side-branch', status: 'completed' }]),
    ];
    // leaf 在主分支 b —— 旁支 t2 不可见
    assert.deepEqual(latestTodosFromEntries(entries, 'b'), [
      { content: 'main-branch', status: 'pending' },
    ]);
    // leaf 在旁支 t2 —— 看到旁支的
    assert.deepEqual(latestTodosFromEntries(entries, 't2'), [
      { content: 'side-branch', status: 'completed' },
    ]);
  });

  it('空清单（已清空）是合法快照', () => {
    const entries = [msg('a', null), todoResult('t1', 'a', [])];
    assert.deepEqual(latestTodosFromEntries(entries, 't1'), []);
  });

  it('非 todo 的 toolResult 被跳过，继续向上找', () => {
    const entries = [
      todoResult('t1', null, [{ content: 'x', status: 'pending' }]),
      {
        id: 'r2',
        parentId: 't1',
        type: 'message',
        message: { role: 'toolResult', toolName: 'bash', details: {} },
      },
      msg('c', 'r2'),
    ];
    assert.deepEqual(latestTodosFromEntries(entries, 'c'), [{ content: 'x', status: 'pending' }]);
  });

  it('leafId 缺失/悬空时返回 undefined 而非抛错', () => {
    const entries = [todoResult('t1', null, [{ content: 'x', status: 'pending' }])];
    assert.equal(latestTodosFromEntries(entries, null), undefined);
    assert.equal(latestTodosFromEntries(entries, 'ghost'), undefined);
  });
});
