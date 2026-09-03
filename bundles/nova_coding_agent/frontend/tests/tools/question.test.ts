/**
 * question 工具渲染器测试（frontend/tui/tools/question.ts，非 dialogs/question.ts）：
 * 等待中显示问题 + 编号选项（含 "Type something." 自由项）+ 等待提示；
 * 完结三态——✓ N. label（index 或 labels 兜底编号）/ ✓ (wrote) 自由输入 /
 * Cancelled；streaming 无结果语义；错误回执。colors 用恒等函数（无 ANSI）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import renderQuestion from '../../tui/tools/question.js';

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

function questionInput(overrides: Record<string, unknown>) {
  return { toolName: 'question', status: 'done' as const, env, ...overrides } as any;
}

describe('question 渲染器（等待中）', () => {
  it('running：问题 + 编号选项 + 等待提示', () => {
    const lines = renderLines(
      renderQuestion(
        questionInput({
          status: 'running',
          args: { question: '选哪个？', options: [{ label: 'A' }, { label: 'B' }] },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('question 选哪个？'));
    assert.ok(text.includes('Options: 1. A, 2. B, 3. Type something.'));
    assert.ok(text.includes('waiting for answer…'));
  });

  it('streaming：问题 + 选项，无等待提示、无结果行', () => {
    const lines = renderLines(
      renderQuestion(
        questionInput({
          status: 'streaming',
          args: { question: '选哪个？', options: [{ label: 'A' }] },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('Options: 1. A, 2. Type something.'));
    assert.ok(!text.includes('waiting'));
    assert.ok(!text.includes('✓'));
    assert.ok(!text.includes('Cancelled'));
  });
});

describe('question 渲染器（完结三态）', () => {
  it('选择：✓ N. label（details.index）', () => {
    const lines = renderLines(
      renderQuestion(
        questionInput({
          result: {
            details: { question: 'q', options: ['A', 'B'], answer: 'B', was_custom: false, index: 2 },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('✓ 2. B'));
    assert.ok(!text.includes('Options:'), '完结后不再显示选项列表');
  });

  it('index 缺失时经 labels 兜底编号；details.options 缺失时回退 args 选项', () => {
    const fromLabels = renderLines(
      renderQuestion(
        questionInput({
          result: { details: { question: 'q', options: ['A', 'B'], answer: 'B', was_custom: false } },
        }),
      ),
    );
    assert.ok(fromLabels.join('\n').includes('✓ 2. B'));

    const fromArgs = renderLines(
      renderQuestion(
        questionInput({
          args: { question: 'q', options: [{ label: 'A' }, { label: 'B' }] },
          result: { details: { question: 'q', answer: 'A', was_custom: false } },
        }),
      ),
    );
    assert.ok(fromArgs.join('\n').includes('✓ 1. A'));
  });

  it('answer 不在 labels 且无 index：无编号直接显示', () => {
    const lines = renderLines(
      renderQuestion(
        questionInput({
          result: { details: { question: 'q', options: ['A', 'B'], answer: 'C', was_custom: false } },
        }),
      ),
    );
    assert.ok(lines.join('\n').includes('✓ C'));
  });

  it('自由输入：✓ (wrote) 答案', () => {
    const lines = renderLines(
      renderQuestion(
        questionInput({
          result: { details: { question: 'q', options: ['A'], answer: '自由', was_custom: true } },
        }),
      ),
    );
    assert.ok(lines.join('\n').includes('✓ (wrote) 自由'));
  });

  it('取消：answer=null 或缺失均显示 Cancelled', () => {
    const nullAnswer = renderLines(
      renderQuestion(questionInput({ result: { details: { question: 'q', options: ['A'], answer: null } } })),
    );
    assert.ok(nullAnswer.join('\n').includes('Cancelled'));

    const missing = renderLines(
      renderQuestion(questionInput({ result: { details: { question: 'q', options: ['A'] } } })),
    );
    assert.ok(missing.join('\n').includes('Cancelled'));
  });
});

describe('question 渲染器（错误回执）', () => {
  it('参数/环境失败直接显示错误文本', () => {
    const lines = renderLines(
      renderQuestion(questionInput({ result: { details: { error: 'ui unavailable' } } })),
    );
    assert.ok(lines.join('\n').includes('ui unavailable'));
  });
});

describe('question 渲染器（多问形态）', () => {
  it('running：逐问显示——已答 ✓、当前问高亮带选项、等待进度', () => {
    const lines = renderLines(
      renderQuestion(
        questionInput({
          status: 'running',
          partial: {
            details: {
              questions: [
                { question: 'Q1', options: ['A', 'B'], answer: 'A', was_custom: false, index: 1 },
                { question: 'Q2', options: ['X', 'Y'] },
                { question: 'Q3', options: ['M'] },
              ],
            },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('✓ 1. A'), '已答问显示回执');
    assert.ok(text.includes('question › Q2'), '当前问（首个未答）高亮');
    assert.ok(text.includes('Options: 1. X, 2. Y, 3. Type something.'), '当前问带选项列表');
    assert.ok(text.includes('Q3'));
    assert.ok(!text.includes('Options: 1. M'), '非当前问不显示选项列表');
    assert.ok(text.includes('waiting for answer… (1/3)'), '等待提示带进度');
  });

  it('完结逐问 ✓ n. label / ✓ (wrote)；不再显示选项列表', () => {
    const lines = renderLines(
      renderQuestion(
        questionInput({
          result: {
            details: {
              questions: [
                { question: 'Q1', options: ['A', 'B'], answer: 'B', was_custom: false, index: 2 },
                { question: 'Q2', options: ['X'], answer: '自由', was_custom: true },
              ],
            },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('✓ 2. B'));
    assert.ok(text.includes('✓ (wrote) 自由'));
    assert.ok(!text.includes('Options:'), '完结后不再显示选项列表');
    assert.ok(!text.includes('Cancelled'), '全部已答不标取消');
  });

  it('取消：存在未答问完结 → Cancelled', () => {
    const lines = renderLines(
      renderQuestion(
        questionInput({
          result: {
            details: {
              questions: [
                { question: 'Q1', options: ['A'], answer: null, was_custom: false },
                { question: 'Q2', options: ['X'], answer: null, was_custom: false },
              ],
            },
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('Q1'));
    assert.ok(text.includes('Q2'));
    assert.ok(text.includes('Cancelled'));
  });

  it('streaming：args.questions 兜底——逐问显示 + 首问选项，无等待/结果语义', () => {
    const lines = renderLines(
      renderQuestion(
        questionInput({
          status: 'streaming',
          args: {
            questions: [
              { question: 'Q1', options: [{ label: 'A' }, { label: 'B' }] },
              { question: 'Q2', options: [{ label: 'X' }] },
            ],
          },
        }),
      ),
    );
    const text = lines.join('\n');
    assert.ok(text.includes('question › Q1'));
    assert.ok(text.includes('Options: 1. A, 2. B, 3. Type something.'));
    assert.ok(text.includes('Q2'));
    assert.ok(!text.includes('waiting'));
    assert.ok(!text.includes('✓'));
  });

  it('index 缺失经 labels 兜底编号（多问条目同单问语义）', () => {
    const lines = renderLines(
      renderQuestion(
        questionInput({
          result: {
            details: {
              questions: [{ question: 'Q1', options: ['A', 'B'], answer: 'B', was_custom: false }],
            },
          },
        }),
      ),
    );
    assert.ok(lines.join('\n').includes('✓ 2. B'));
  });
});
