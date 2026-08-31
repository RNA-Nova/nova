/**
 * subagent / todo 渲染器冒烟测试（frontend/tui/tools/{subagent,todo}.ts）：
 * 组件形态产出 + render(width) 真实渲染行，覆盖三模式、运行中占位、
 * 折叠/展开、错误回执与 todo 清单语义。colors 用恒等函数（无 ANSI）。
 * 夹具为新契约形状：线上 ToolCallItem 包进 { item, env? }。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { RendererInput } from 'nova-client';

import renderSubagent from '../../tui/tools/subagent.js';
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

/** 新契约夹具：构造线上 ToolCallItem 包进 { item, env? }。 */
function toolInput(
  tool: string,
  o: {
    status?: RendererInput['item']['status'];
    args?: unknown;
    result?: unknown;
    partialResult?: unknown;
    env?: RendererInput['env'];
  },
): RendererInput {
  return {
    item: {
      id: 'tc-1',
      type: 'toolCall',
      status: o.status ?? 'done',
      source: null,
      ts: 0,
      tool,
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

function subagentInput(o: Parameters<typeof toolInput>[1]): RendererInput {
  return toolInput('subagent', o);
}

describe('subagent 渲染器', () => {
  it('pending 态渲染调用头部（parallel 规模）', () => {
    const lines = renderLines(
      renderSubagent(
        subagentInput({
          status: 'pending',
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
          partialResult: {
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
    assert.ok(text.includes('(running...)'));
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

describe('todo 渲染器', () => {
  it('折叠态：进度行 + 前 5 条 + 余量提示', () => {
    const todos = Array.from({ length: 7 }, (_, i) => ({
      content: `task ${i + 1}`,
      status: i < 2 ? 'completed' : i === 2 ? 'in_progress' : 'pending',
    }));
    const lines = renderLines(
      renderTodo(toolInput('todo', { result: { details: { todos } } })),
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
      renderTodo(
        toolInput('todo', { env: { ...env, expanded: true }, result: { details: { todos } } }),
      ),
    );
    assert.ok(lines.join('\n').includes('task 7'));
  });

  it('错误回执标错', () => {
    const lines = renderLines(
      renderTodo(toolInput('todo', { status: 'failed', result: { details: { error: 'bad status' } } })),
    );
    assert.ok(lines.join('\n').includes('Error: bad status'));
  });
});

describe('question 渲染器', () => {
  it('等待中：问题 + 编号选项 + 等待提示', async () => {
    const renderQuestion = (await import('../../tui/tools/question.js')).default;
    const lines = renderLines(
      renderQuestion(
        toolInput('question', {
          status: 'running',
          args: { question: '选哪个？', options: [{ label: 'A' }, { label: 'B' }] },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('选哪个？'));
    assert.ok(text.includes('1. A'));
    assert.ok(text.includes('3. Type something.'));
    assert.ok(text.includes('waiting'));
  });

  it('选择完成：✓ N. label', async () => {
    const renderQuestion = (await import('../../tui/tools/question.js')).default;
    const lines = renderLines(
      renderQuestion(
        toolInput('question', {
          result: {
            details: { question: 'q', options: ['A', 'B'], answer: 'B', was_custom: false, index: 2 },
          },
        }),
      ),
    );
    assert.ok(lines.join('\n').includes('✓ 2. B'));
  });

  it('自由输入：✓ (wrote) 答案；取消：Cancelled', async () => {
    const renderQuestion = (await import('../../tui/tools/question.js')).default;
    const custom = renderLines(
      renderQuestion(
        toolInput('question', {
          result: { details: { question: 'q', options: ['A'], answer: '自由', was_custom: true } },
        }),
      ),
    );
    assert.ok(custom.join('\n').includes('(wrote) 自由'));
    const cancelled = renderLines(
      renderQuestion(
        toolInput('question', {
          result: { details: { question: 'q', options: ['A'], answer: null } },
        }),
      ),
    );
    assert.ok(cancelled.join('\n').includes('Cancelled'));
  });
});

describe('bash 渲染器计时', () => {
  it('running 态不再自带计时行（计时归宿主 ElapsedLine chrome）', async () => {
    const renderBash = (await import('../../tui/tools/bash.js')).default;
    const lines = renderLines(
      renderBash(toolInput('bash', { status: 'running', args: { command: 'sleep 10' } })),
    );
    assert.ok(!lines.join('\n').includes('Running…'));
  });
});
