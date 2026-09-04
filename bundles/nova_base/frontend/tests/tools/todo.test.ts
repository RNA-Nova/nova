/**
 * todo 渲染器冒烟测试（frontend/tui/tools/todo.ts）：
 * 组件形态产出 + render(width) 真实渲染行，覆盖折叠/展开与错误回执的
 * 清单语义。colors 用恒等函数（无 ANSI）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import renderTodo from '../../tui/tools/todo.js';

const identity = (s: string) => s;
const env = {
  cwd: '/tmp',
  colors: new Proxy({}, { get: () => identity }) as Record<string, (s: string) => string>,
  expanded: false,
};

function renderLines(output: unknown, width = 100): string[] {
  assert.ok(
    typeof output === 'object' && output !== null && typeof (output as any).render === 'function',
    '渲染器应产出组件形态',
  );
  return (output as { render: (w: number) => string[] }).render(width);
}

describe('todo 渲染器', () => {
  it('折叠态：进度行 + 前 5 条 + 余量提示', () => {
    const todos = Array.from({ length: 7 }, (_, i) => ({
      content: `task ${i + 1}`,
      status: i < 2 ? 'completed' : i === 2 ? 'in_progress' : 'pending',
    }));
    const lines = renderLines(
      renderTodo({ toolName: 'todo', status: 'done', env, result: { details: { todos } } } as any),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('2/7 completed'));
    assert.ok(text.includes('1 in progress'));
    assert.ok(text.includes('task 5'));
    assert.ok(!text.includes('task 6'));
    assert.ok(text.includes('... 2 more'));
  });

  it('展开态显示全量', () => {
    const todos = Array.from({ length: 7 }, (_, i) => ({ content: `task ${i + 1}`, status: 'pending' }));
    const lines = renderLines(
      renderTodo({
        toolName: 'todo',
        status: 'done',
        env: { ...env, expanded: true },
        result: { details: { todos } },
      } as any),
    );
    assert.ok(lines.join('\n').includes('task 7'));
  });

  it('错误回执标错', () => {
    const lines = renderLines(
      renderTodo({ toolName: 'todo', status: 'error', env, result: { details: { error: 'bad status' } } } as any),
    );
    assert.ok(lines.join('\n').includes('Error: bad status'));
  });
});
