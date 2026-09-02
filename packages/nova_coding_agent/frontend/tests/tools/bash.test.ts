/**
 * bash 渲染器测试（frontend/tui/tools/bash.ts）：
 * 折叠态末 5 视觉行 + 隐藏行数提示、视觉行折行语义、running 计时（env.elapsedMs）
 * vs 完结 Took、exit code 非零、截断警告（含全量输出路径）、full-output footer
 * 剥离、stderr 与错误回执。colors 用恒等函数（无 ANSI）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import renderBash from '../../tui/tools/bash.js';

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

function bashInput(overrides: Record<string, unknown>) {
  return { toolName: 'bash', status: 'done' as const, env, ...overrides } as any;
}

describe('bash 渲染器', () => {
  it('折叠态：末 5 视觉行 + 隐藏行数提示 + $ 命令行', () => {
    const stdout = Array.from({ length: 8 }, (_, i) => `L0${i + 1}`).join('\n');
    const lines = renderLines(
      renderBash(
        bashInput({
          result: { details: { command: 'npm test', stdout, exit_code: 0, duration_ms: 1234 } },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('$ npm test'));
    assert.ok(text.includes('... (3 earlier lines, ctrl+o to expand)'));
    assert.ok(text.includes('L04'));
    assert.ok(text.includes('L08'));
    assert.ok(!text.includes('L03'), '前 3 行应被折叠隐藏');
  });

  it('折叠按视觉行计算：长行折行后计入隐藏行数', () => {
    // 40 个 x 在宽 30（正文宽 28）下折成 2 视觉行 + 4 短行 = 6 → 隐藏 1
    const stdout = `${'x'.repeat(40)}\nL1\nL2\nL3\nL4`;
    const lines = renderLines(
      renderBash(bashInput({ result: { details: { stdout, exit_code: 0 } } })),
      30,
    );
    const text = lines.join('\n');
    assert.ok(text.includes('(1 earlier'));
    assert.ok(!text.includes('x'.repeat(28)), '折行第一段应被隐藏');
    assert.ok(text.includes('x'.repeat(12)), '折行末段在末 5 视觉行内');
  });

  it('展开态：全量输出且无隐藏提示', () => {
    const stdout = Array.from({ length: 8 }, (_, i) => `L0${i + 1}`).join('\n');
    const lines = renderLines(
      renderBash(
        bashInput({
          env: { ...env, expanded: true },
          result: { details: { stdout, exit_code: 0 } },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('L01'));
    assert.ok(text.includes('L08'));
    assert.ok(!text.includes('earlier lines'));
  });

  it('running 态不再自带计时行（计时归宿主 ElapsedLine chrome）', () => {
    const lines = renderLines(
      renderBash(
        bashInput({
          status: 'running',
          partial: { details: { command: 'sleep 10' } },
        }),
      ),
    );
    assert.ok(!lines.join('\n').includes('Running…'));
  });

  it('完结显示 Took X.Xs（duration_ms）', () => {
    const lines = renderLines(
      renderBash(bashInput({ result: { details: { command: 'ls', exit_code: 0, duration_ms: 1234 } } })),
    );
    assert.ok(lines.join('\n').includes('Took 1.2s'));
  });

  it('exit code 非零单独成行', () => {
    const lines = renderLines(
      renderBash(bashInput({ result: { details: { command: 'false', exit_code: 2 } } })),
    );
    assert.ok(lines.join('\n').includes('exit code 2'));
  });

  it('截断警告带完整输出路径；无路径时退化为纯警告', () => {
    const withPath = renderLines(
      renderBash(
        bashInput({
          result: {
            details: { command: 'c', stdout: 'o', exit_code: 0, truncated: true, full_output_path: '/tmp/full.log' },
          },
        }),
      ),
    );
    assert.ok(withPath.join('\n').includes('输出已截断，完整内容见 /tmp/full.log'));

    const noPath = renderLines(
      renderBash(
        bashInput({ result: { details: { command: 'c', stdout: 'o', exit_code: 0, truncated: true } } }),
      ),
    );
    const text = noPath.join('\n');
    assert.ok(text.includes('输出已截断'));
    assert.ok(!text.includes('完整内容见'));
  });

  it('展示前剥掉输出末尾的 full-output footer（避免与警告行重复）', () => {
    const lines = renderLines(
      renderBash(
        bashInput({
          result: {
            details: {
              command: 'c',
              stdout: 'hello\n[output truncated. Full output: /tmp/full.log]',
              exit_code: 0,
              truncated: true,
              full_output_path: '/tmp/full.log',
            },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('hello'));
    assert.ok(!text.includes('Full output'), 'footer 不应与截断警告重复出现');
  });

  it('stderr 与错误回执', () => {
    const withStderr = renderLines(
      renderBash(bashInput({ result: { details: { command: 'c', stderr: 'warn me', exit_code: 0 } } })),
    );
    assert.ok(withStderr.join('\n').includes('warn me'));

    const failed = renderLines(renderBash(bashInput({ result: { details: { error: 'spawn failed' } } })));
    assert.ok(failed.join('\n').includes('执行失败：spawn failed'));
  });
});
