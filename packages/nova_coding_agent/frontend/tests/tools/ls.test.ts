/**
 * ls 渲染器测试（frontend/tui/tools/ls.ts）：
 * 折叠态前 20 行预览 + 超出提示、展开态全量、截断警告、
 * 空结果、partial 回退、错误回执。
 * colors 用恒等函数（无 ANSI）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { RendererInput } from 'nova-client';

import renderLs from '../../tui/tools/ls.js';

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

/** 新契约夹具：构造线上 ToolCallItem 包进 { item, env? }。 */
function lsInput(o: {
  status?: RendererInput['item']['status'];
  args?: unknown;
  result?: unknown;
  partialResult?: unknown;
  env?: RendererInput['env'];
}): RendererInput {
  return {
    item: {
      id: 'tc-1',
      type: 'toolCall',
      status: o.status ?? 'done',
      source: null,
      ts: 0,
      tool: 'ls',
      args: o.args ?? {},
      argsComplete: true,
      result: o.result ?? null,
      partialResult: o.partialResult ?? null,
      durationMs: null,
      error: null,
    },
    env: o.env ?? env,
  };
}

function textContent(text: string) {
  return [{ type: 'text', text }];
}

describe('ls 渲染器', () => {
  it('折叠态：前 20 行预览 + 超出提示', () => {
    const text = Array.from({ length: 23 }, (_, i) => `entry${String(i + 1).padStart(2, '0')}/`).join('\n');
    const lines = renderLines(renderLs(lsInput({ result: { content: textContent(text), details: {} } })));
    const out = lines.join('\n');
    assert.ok(out.includes('entry20/'));
    assert.ok(!out.includes('entry21/'), '第 21 行起应被折叠');
    assert.ok(out.includes('... (3 more, ctrl+o to expand)'));
  });

  it('展开态：全量', () => {
    const text = Array.from({ length: 23 }, (_, i) => `entry${String(i + 1).padStart(2, '0')}/`).join('\n');
    const lines = renderLines(
      renderLs(lsInput({ env: { ...env, expanded: true }, result: { content: textContent(text), details: {} } })),
    );
    const out = lines.join('\n');
    assert.ok(out.includes('entry23/'));
    assert.ok(!out.includes('more, ctrl+o'));
  });

  it('截断警告', () => {
    const lines = renderLines(
      renderLs(lsInput({ result: { content: textContent('a/'), details: { truncated: true } } })),
    );
    assert.ok(lines.join('\n').includes('条目过多已截断——可提高 limit 重试'));
  });

  it('空结果渲染为空卡片', () => {
    const lines = renderLines(renderLs(lsInput({ result: { content: textContent(''), details: {} } })));
    assert.deepEqual(lines, []);
  });

  it('running 态结果未至时回退 partialResult.content', () => {
    const lines = renderLsRender('running');
    assert.ok(lines.includes('partial-entry/'));
  });

  it('错误回执', () => {
    const lines = renderLines(renderLs(lsInput({ result: { details: { error: 'not a directory' } } })));
    assert.ok(lines.join('\n').includes('列目录失败：not a directory'));
  });
});

/** running 态 partialResult 回退的辅助（避免重复构造输入）。 */
function renderLsRender(status: 'running'): string {
  const lines = renderLines(
    renderLs(lsInput({ status, partialResult: { content: textContent('partial-entry/') } })),
  );
  return lines.join('\n');
}
