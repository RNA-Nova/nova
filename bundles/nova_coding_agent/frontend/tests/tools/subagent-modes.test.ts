/**
 * subagent 渲染器冒烟测试（frontend/tui/tools/subagent.ts）：
 * 组件形态产出 + render(width) 真实渲染行，覆盖三模式、运行中占位、
 * 折叠/展开与错误回执。colors 用恒等函数（无 ANSI）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import renderSubagent from '../../tui/tools/subagent.js';

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

function subagentInput(overrides: Record<string, unknown>) {
  return { toolName: 'subagent', status: 'done' as const, env, ...overrides } as any;
}

describe('subagent 渲染器', () => {
  it('streaming 态渲染调用头部（parallel 规模）', () => {
    const lines = renderLines(
      renderSubagent(
        subagentInput({
          status: 'streaming',
          args: { tasks: [{ agent: 'scout', task: 'find auth code' }] },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('parallel (1 tasks)'));
    assert.ok(text.includes('scout'));
  });

  it('single 完成：图标 + 来源 + usage 行', () => {
    const lines = renderLines(
      renderSubagent(
        subagentInput({
          result: {
            details: {
              mode: 'single',
              results: [
                {
                  agent: 'scout',
                  agent_source: 'package',
                  task: 'find code',
                  output: 'done',
                  exit_code: 0,
                  usage: { turns: 2, input_tokens: 1500, cost: 0.001 },
                  model: 'm1',
                  messages: [],
                },
              ],
            },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('✓'));
    assert.ok(text.includes('scout'));
    assert.ok(text.includes('(package)'));
    assert.ok(text.includes('2 turns'));
    assert.ok(text.includes('↑1.5k'));
  });

  it('parallel 运行中：占位 ⏳ + 进度状态行', () => {
    const lines = renderLines(
      renderSubagent(
        subagentInput({
          status: 'running',
          partial: {
            details: {
              mode: 'parallel',
              results: [
                { agent: 'scout', task: 'a', exit_code: -1, messages: [] },
                {
                  agent: 'worker',
                  task: 'b',
                  exit_code: 0,
                  output: 'ok',
                  messages: [],
                },
              ],
            },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('1/2 done, 1 running'));
    assert.ok(text.includes('⏳'));
    assert.ok(!text.includes('(running...)'), '⏳ 已表达运行中——不再重复占位文本');
  });

  it('失败结果：✗ + stop_reason + 错误行', () => {
    const lines = renderLines(
      renderSubagent(
        subagentInput({
          result: {
            details: {
              mode: 'single',
              results: [
                {
                  agent: 'worker',
                  task: 'x',
                  exit_code: 1,
                  error: 'boom',
                  error_message: 'boom',
                  stop_reason: 'error',
                  messages: [],
                },
              ],
            },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('✗'));
    assert.ok(text.includes('[error]'));
    assert.ok(text.includes('Error: boom'));
  });

  it('chain 折叠态：步骤行 + 工具调用格式化', () => {
    const lines = renderLines(
      renderSubagent(
        subagentInput({
          result: {
            details: {
              mode: 'chain',
              results: [
                {
                  agent: 'scout',
                  task: 'a',
                  exit_code: 0,
                  usage: {},
                  messages: [
                    {
                      role: 'assistant',
                      content: [
                        { type: 'toolCall', name: 'grep', arguments: { pattern: 'auth', path: 'src' } },
                        { type: 'text', text: 'found it' },
                      ],
                    },
                  ],
                },
                { agent: 'planner', task: 'b', exit_code: 0, usage: {}, messages: [] },
              ],
            },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('chain'));
    assert.ok(text.includes('Step 1'));
    assert.ok(text.includes('→ grep /auth/ in src'));
    assert.ok(text.includes('(ctrl+o to expand)'));
  });
});
