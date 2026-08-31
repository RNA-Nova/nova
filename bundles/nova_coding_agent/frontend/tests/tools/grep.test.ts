/**
 * grep 渲染器测试（frontend/tui/tools/grep.ts）：
 * 折叠态前 15 行预览 + 超出提示、展开态全量、截断警告（truncated 与
 * match_limit_reached 双触发）、空结果、错误回执。
 * colors 用恒等函数（无 ANSI）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { RendererInput } from 'nova-client';

import renderGrep from '../../tui/tools/grep.js';

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
function grepInput(o: {
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
      tool: 'grep',
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

describe('grep 渲染器', () => {
  it('折叠态：前 15 行预览 + 超出提示', () => {
    const text = Array.from({ length: 18 }, (_, i) => `src/a.ts:${i + 1}: match`).join('\n');
    const lines = renderLines(renderGrep(grepInput({ result: { content: textContent(text), details: {} } })));
    const out = lines.join('\n');
    assert.ok(out.includes('src/a.ts:15: match'));
    assert.ok(!out.includes('src/a.ts:16: match'), '第 16 行起应被折叠');
    assert.ok(out.includes('... (3 more, ctrl+o to expand)'));
  });

  it('展开态：全量', () => {
    const text = Array.from({ length: 18 }, (_, i) => `src/a.ts:${i + 1}: match`).join('\n');
    const lines = renderLines(
      renderGrep(grepInput({ env: { ...env, expanded: true }, result: { content: textContent(text), details: {} } })),
    );
    const out = lines.join('\n');
    assert.ok(out.includes('src/a.ts:18: match'));
    assert.ok(!out.includes('more, ctrl+o'));
  });

  it('截断警告：truncated 与 match_limit_reached 均触发', () => {
    const truncated = renderLines(
      renderGrep(grepInput({ result: { content: textContent('m'), details: { truncated: true } } })),
    );
    assert.ok(truncated.join('\n').includes('结果过多已截断——可提高 limit 或收窄 pattern'));

    const limitReached = renderLines(
      renderGrep(grepInput({ result: { content: textContent('m'), details: { match_limit_reached: true } } })),
    );
    assert.ok(limitReached.join('\n').includes('结果过多已截断——可提高 limit 或收窄 pattern'));
  });

  it('空结果渲染为空卡片', () => {
    const lines = renderLines(renderGrep(grepInput({ result: { content: textContent(''), details: {} } })));
    assert.deepEqual(lines, []);
  });

  it('错误回执', () => {
    const lines = renderLines(renderGrep(grepInput({ result: { details: { error: 'invalid regex' } } })));
    assert.ok(lines.join('\n').includes('搜索失败：invalid regex'));
  });
});
