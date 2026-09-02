/**
 * write 渲染器测试（frontend/tui/tools/write.ts）：
 * 折叠态前 10 行预览 + 余量提示（行数从 args.content 算）、展开态全量、
 * ≤10 行全量无提示、content 缺失/非字符串的明确提示行与成功静默、错误回执。
 * colors 用恒等函数（无 ANSI）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import renderWrite from '../../tui/tools/write.js';

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

function writeInput(overrides: Record<string, unknown>) {
  return { toolName: 'write', status: 'done' as const, env, ...overrides } as any;
}

const content15 = Array.from({ length: 15 }, (_, i) => `w${i + 1}`).join('\n');

describe('write 渲染器', () => {
  it('折叠态：前 10 行预览 + 余量提示（行数从 args.content 算）', () => {
    const lines = renderLines(renderWrite(writeInput({ args: { path: 'f', content: content15 } })));
    const text = lines.join('\n');
    assert.ok(text.includes('w10'));
    assert.ok(!text.includes('w11'), '第 11 行起应被折叠');
    assert.ok(text.includes('... (5 more lines, 15 total, ctrl+o to expand)'));
  });

  it('展开态：全量内容', () => {
    const lines = renderLines(
      renderWrite(writeInput({ env: { ...env, expanded: true }, args: { path: 'f', content: content15 } })),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('w15'));
    assert.ok(!text.includes('more lines'));
  });

  it('≤10 行：全量显示且无余量提示', () => {
    const content = Array.from({ length: 10 }, (_, i) => `w${i + 1}`).join('\n');
    const lines = renderLines(renderWrite(writeInput({ args: { path: 'f', content } })));
    const text = lines.join('\n');
    assert.ok(text.includes('w10'));
    assert.ok(!text.includes('more lines'));
  });

  it('content 非字符串：明确提示行；成功且无 content 静默', () => {
    // running 态参数未到齐 / done 态 content 为非字符串 → 提示行
    const running = renderLines(renderWrite(writeInput({ status: 'running', args: { path: 'f' } })));
    assert.ok(running.join('\n').includes('[invalid content arg - expected string]'));

    const wrongType = renderLines(renderWrite(writeInput({ args: { path: 'f', content: 123 } })));
    assert.ok(wrongType.join('\n').includes('[invalid content arg - expected string]'));

    // 成功结果且 content 缺失——静默（空卡片）
    const silent = renderLines(renderWrite(writeInput({ args: { path: 'f' } })));
    assert.deepEqual(silent, []);
  });

  it('错误结果行', () => {
    const lines = renderLines(renderWrite(writeInput({ result: { details: { error: 'permission denied' } } })));
    assert.ok(lines.join('\n').includes('写入失败：permission denied'));
  });
});
