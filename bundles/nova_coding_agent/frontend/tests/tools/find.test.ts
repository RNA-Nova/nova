/**
 * find 渲染器测试（frontend/tui/tools/find.ts）：
 * 折叠态前 20 行预览 + 超出提示、展开态全量、截断警告、
 * 空行过滤与空结果、partial 回退（running 态结果未至）、错误回执。
 * colors 用恒等函数（无 ANSI）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import renderFind from '../../tui/tools/find.js';

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

function findInput(overrides: Record<string, unknown>) {
  return { toolName: 'find', status: 'done' as const, env, ...overrides } as any;
}

function textContent(text: string) {
  return [{ type: 'text', text }];
}

describe('find 渲染器', () => {
  it('折叠态：前 20 行预览 + 超出提示', () => {
    const text = Array.from({ length: 25 }, (_, i) => `src/f${String(i + 1).padStart(2, '0')}.ts`).join('\n');
    const lines = renderLines(renderFind(findInput({ result: { content: textContent(text), details: {} } })));
    const out = lines.join('\n');
    assert.ok(out.includes('src/f20.ts'));
    assert.ok(!out.includes('src/f21.ts'), '第 21 行起应被折叠');
    assert.ok(out.includes('... (5 more, ctrl+o to expand)'));
  });

  it('展开态：全量', () => {
    const text = Array.from({ length: 25 }, (_, i) => `src/f${String(i + 1).padStart(2, '0')}.ts`).join('\n');
    const lines = renderLines(
      renderFind(findInput({ env: { ...env, expanded: true }, result: { content: textContent(text), details: {} } })),
    );
    const out = lines.join('\n');
    assert.ok(out.includes('src/f25.ts'));
    assert.ok(!out.includes('more, ctrl+o'));
  });

  it('截断警告', () => {
    const lines = renderLines(
      renderFind(findInput({ result: { content: textContent('a.ts'), details: { truncated: true } } })),
    );
    assert.ok(lines.join('\n').includes('结果过多已截断——可提高 limit 重试'));
  });

  it('空行被过滤；空结果渲染为空卡片', () => {
    const filtered = renderLines(
      renderFind(findInput({ result: { content: textContent('a\n\nb'), details: {} } })),
    );
    assert.deepEqual(
      filtered.map((l) => l.trim()),
      ['a', 'b'],
    );

    const empty = renderLines(renderFind(findInput({ result: { content: textContent(''), details: {} } })));
    assert.deepEqual(empty, []);
  });

  it('running 态结果未至时回退 partial.content', () => {
    const lines = renderLines(
      renderFind(findInput({ status: 'running', partial: { content: textContent('p1\np2') } })),
    );
    const out = lines.join('\n');
    assert.ok(out.includes('p1'));
    assert.ok(out.includes('p2'));
  });

  it('错误回执', () => {
    const lines = renderLines(renderFind(findInput({ result: { details: { error: 'bad pattern' } } })));
    assert.ok(lines.join('\n').includes('查找失败：bad pattern'));
  });
});
