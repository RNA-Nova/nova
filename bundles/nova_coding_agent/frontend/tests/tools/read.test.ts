/**
 * read 渲染器测试（frontend/tui/tools/read.ts）：
 * 折叠态内容区为空（pi 语义）+ 截断提示、展开态全量渲染（Markdown fence）
 * 与 meta 表（total_lines / truncated / mime / resized）、错误回执。
 * 展开态 Markdown 组件需要主题，用恒等函数 Proxy 假主题（codeBlockIndent 除外）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import renderRead from '../../tui/tools/read.js';

const identity = (s: string) => s;
const env = {
  cwd: '/tmp',
  colors: new Proxy({}, { get: () => identity }) as Record<string, (s: string) => string>,
  expanded: false,
};

/** Markdown 假主题：全部色函数恒等；highlightCode 缺省走 codeBlock 通道。 */
const markdownTheme = new Proxy(
  {},
  { get: (_t, p) => (p === 'codeBlockIndent' ? '  ' : p === 'highlightCode' ? undefined : identity) },
);

function renderLines(output: unknown, width = 100): string[] {
  assert.ok(
    typeof output === 'object' && output !== null && typeof (output as any).render === 'function',
    '渲染器应产出组件形态',
  );
  return (output as { render: (w: number) => string[] }).render(width);
}

function readInput(overrides: Record<string, unknown>) {
  return { toolName: 'read', status: 'done' as const, env, ...overrides } as any;
}

describe('read 渲染器（折叠态——pi 语义内容区为空）', () => {
  it('truncated 时只显示折叠提示，内容不外泄', () => {
    const lines = renderLines(
      renderRead(
        readInput({
          result: {
            content: [{ type: 'text', text: 'file body secret' }],
            details: { path: 'x.ts', total_lines: 1000, truncated: true, truncated_by: 'lines' },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('（内容已折叠——ctrl+o 展开查看；共 of 1000 行）'));
    assert.ok(!text.includes('file body secret'), '折叠态不应渲染内容');
  });

  it('truncated 但无 total_lines：提示不含行数', () => {
    const lines = renderLines(
      renderRead(
        readInput({
          result: { content: [{ type: 'text', text: 'body' }], details: { truncated: true } },
        }),
      ),
    );
    assert.ok(lines.join('\n').includes('（内容已折叠——ctrl+o 展开查看；共 行）'));
  });

  it('未截断且未展开：完全空卡片（连提示都没有）', () => {
    const lines = renderLines(
      renderRead(
        readInput({ result: { content: [{ type: 'text', text: 'body' }], details: { total_lines: 5 } } }),
      ),
    );
    assert.deepEqual(lines, []);
  });
});

describe('read 渲染器（展开态）', () => {
  it('渲染内容（Markdown fence）+ 路径行 + meta 表', () => {
    const lines = renderLines(
      renderRead(
        readInput({
          env: { ...env, expanded: true, markdownTheme },
          result: {
            content: [{ type: 'text', text: 'line A\nline B' }],
            details: { path: 'x.ts', total_lines: 100, truncated: true, truncated_by: 'bytes' },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('line A'));
    assert.ok(text.includes('line B'));
    assert.ok(text.includes('x.ts'));
    // meta 表：truncated_by=bytes 呈现为 50KB limit
    assert.ok(text.includes('field'));
    assert.ok(text.includes('total_lines'));
    assert.ok(text.includes('100'));
    assert.ok(text.includes('truncated'));
    assert.ok(text.includes('50KB limit'));
  });

  it('图片回执：mime / resized 进 meta 表，无内容区', () => {
    const lines = renderLines(
      renderRead(
        readInput({
          env: { ...env, expanded: true, markdownTheme },
          result: { details: { path: 'p.png', mime: 'image/png', size: 5000, resized: true } },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('mime'));
    assert.ok(text.includes('image/png'));
    assert.ok(text.includes('resized'));
    assert.ok(!text.includes('total_lines'));
  });

  it('truncated_by 非 bytes 原样呈现', () => {
    const lines = renderLines(
      renderRead(
        readInput({
          env: { ...env, expanded: true, markdownTheme },
          result: {
            content: [{ type: 'text', text: 'body' }],
            details: { truncated: true, truncated_by: 'lines' },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('truncated'));
    assert.ok(text.includes('lines'));
    assert.ok(!text.includes('50KB limit'));
  });
});

describe('read 渲染器（错误回执）', () => {
  it('读取失败标错', () => {
    const lines = renderLines(renderRead(readInput({ result: { details: { error: 'file not found' } } })));
    assert.ok(lines.join('\n').includes('读取失败：file not found'));
  });
});
