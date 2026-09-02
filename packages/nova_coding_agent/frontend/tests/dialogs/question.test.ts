/**
 * QuestionDialog 组件测试（frontend/tui/dialogs/question.ts）：
 * 选项导航/选择（answer + wasCustom=false + index）、自由项进编辑态、
 * esc 编辑态返回/选项态取消、onSubmit 空串回退、工厂参数归一化。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  QuestionDialog,
  QuestionnaireDialog,
  questionDialogFactory,
} from '../../tui/dialogs/question.js';

const fakeTui = {} as never;

function makeDialog(
  done: (result?: { answer: string; wasCustom: boolean; index?: number }) => void,
  options = [{ label: 'A 方案' }, { label: 'B 方案', description: '慢一点' }],
) {
  return new QuestionDialog(fakeTui, '选哪个？', options, done);
}

// 键位以终端转义序列驱动（与真实输入同形态）
const DOWN = '\x1b[B';
const UP = '\x1b[A';
const ENTER = '\r';
const ESC = '\x1b';

describe('QuestionDialog', () => {
  it('enter 选择首项：answer + wasCustom=false + index=1', () => {
    const results: unknown[] = [];
    const dialog = makeDialog((r) => results.push(r));
    dialog.handleInput(ENTER);
    assert.deepEqual(results, [{ answer: 'A 方案', wasCustom: false, index: 1 }]);
  });

  it('↓ 移动后选择第二项：index=2', () => {
    const results: unknown[] = [];
    const dialog = makeDialog((r) => results.push(r));
    dialog.handleInput(DOWN);
    dialog.handleInput(ENTER);
    assert.deepEqual(results, [{ answer: 'B 方案', wasCustom: false, index: 2 }]);
  });

  it('选中 "Type something." 进编辑态；esc 返回选项态', () => {
    const results: unknown[] = [];
    const dialog = makeDialog((r) => results.push(r));
    dialog.handleInput(DOWN); // B
    dialog.handleInput(DOWN); // Type something.
    dialog.handleInput(ENTER); // 进编辑态
    assert.equal((dialog as any).editMode, true);
    dialog.handleInput(ESC); // 编辑态 esc 返回
    assert.equal((dialog as any).editMode, false);
    assert.deepEqual(results, []); // 无提交
  });

  it('编辑态 onSubmit 文本 → wasCustom=true；空串不提交', () => {
    const results: unknown[] = [];
    const dialog = makeDialog((r) => results.push(r));
    (dialog as any).editMode = true;
    (dialog as any).editor.onSubmit('  ');
    assert.deepEqual(results, []);
    assert.equal((dialog as any).editMode, false);
    (dialog as any).editMode = true;
    (dialog as any).editor.onSubmit('  自定义答案  ');
    assert.deepEqual(results, [{ answer: '自定义答案', wasCustom: true }]);
  });

  it('选项态 esc 取消：done(undefined)', () => {
    const results: unknown[] = [];
    const dialog = makeDialog((r) => results.push(r));
    dialog.handleInput(ESC);
    assert.deepEqual(results, [undefined]);
  });

  it('render 输出含问题与编号选项（含自由项）', () => {
    const dialog = makeDialog(() => {});
    const text = dialog.render(60).join('\n');
    assert.ok(text.includes('选哪个？'));
    assert.ok(text.includes('1. A 方案'));
    assert.ok(text.includes('3. Type something.'));
    assert.ok(text.includes('慢一点'));
  });
});

describe('questionDialogFactory', () => {
  it('参数归一化：非对象/无 label 选项被过滤', () => {
    let captured: unknown;
    const component = questionDialogFactory(
      { tui: fakeTui },
      {
        question: 'q',
        options: [{ label: 'A' }, 'bogus', { label: 1 }, { label: 'B', description: 'd' }],
      },
      (r) => (captured = r),
    ) as QuestionDialog;
    component.handleInput(DOWN);
    component.handleInput(ENTER);
    assert.deepEqual(captured, { answer: 'B', wasCustom: false, index: 2 });
  });

  it('缺失参数不炸（空问题 + 空选项只剩自由项）', () => {
    const component = questionDialogFactory({ tui: fakeTui }, {}, () => {}) as QuestionDialog;
    const text = component.render(50).join('\n');
    assert.ok(text.includes('1. Type something.'));
  });
});

// 多问键位（与真实输入同形态）
const TAB = '\t';
const SHIFT_TAB = '\x1b[Z';
const LEFT = '\x1b[D';
const RIGHT = '\x1b[C';

function makeQuestionnaire(
  done: (result?: { answers: Array<{ answer: string; wasCustom: boolean; index?: number }> }) => void,
  questions = [
    { question: 'Q1 选哪个？', options: [{ label: 'A' }, { label: 'B', description: '慢' }] },
    { question: 'Q2 选哪个？', options: [{ label: 'X' }, { label: 'Y' }] },
  ],
) {
  return new QuestionnaireDialog(fakeTui, questions, done);
}

describe('QuestionnaireDialog（多问形态）', () => {
  it('enter 答当前问：记录 + 自动跳下一未答问，未答完不提交', () => {
    const results: unknown[] = [];
    const dialog = makeQuestionnaire((r) => results.push(r));
    dialog.handleInput(ENTER); // Q1 → A
    assert.deepEqual(results, [], '未答完不得提交');
    assert.equal((dialog as any).currentPage, 1, '答完自动跳下一未答问');
    dialog.handleInput(DOWN);
    dialog.handleInput(ENTER); // Q2 → Y（index=2）
    assert.deepEqual(results, [], '答完最后一问仍需再一次 enter 提交');
  });

  it('全部答完后 enter 提交：answers 按问序归集 + index 语义', () => {
    const results: unknown[] = [];
    const dialog = makeQuestionnaire((r) => results.push(r));
    dialog.handleInput(ENTER); // Q1 → A（index=1）
    dialog.handleInput(DOWN);
    dialog.handleInput(ENTER); // Q2 → Y（index=2）
    dialog.handleInput(ENTER); // 全答完 → 提交
    assert.deepEqual(results, [
      {
        answers: [
          { answer: 'A', wasCustom: false, index: 1 },
          { answer: 'Y', wasCustom: false, index: 2 },
        ],
      },
    ]);
  });

  it('tab 条渲染：已答 ✓ 标记', () => {
    const dialog = makeQuestionnaire(() => {});
    const before = dialog.render(60).join('\n');
    assert.ok(before.includes('[1]'));
    assert.ok(before.includes('[2]'));
    assert.ok(!before.includes('✓'));
    dialog.handleInput(ENTER); // 答 Q1
    const after = dialog.render(60).join('\n');
    assert.ok(after.includes('[1✓]'));
    assert.ok(after.includes('[2]'));
  });

  it('tab/→ 下一页、shift+tab/← 上一页（循环）', () => {
    const dialog = makeQuestionnaire(() => {});
    assert.equal((dialog as any).currentPage, 0);
    dialog.handleInput(TAB);
    assert.equal((dialog as any).currentPage, 1);
    dialog.handleInput(TAB); // 循环回首页
    assert.equal((dialog as any).currentPage, 0);
    dialog.handleInput(RIGHT);
    assert.equal((dialog as any).currentPage, 1);
    dialog.handleInput(LEFT);
    assert.equal((dialog as any).currentPage, 0);
    dialog.handleInput(SHIFT_TAB); // 循环到末页
    assert.equal((dialog as any).currentPage, 1);
  });

  it('切回已答页：光标归位已答选项', () => {
    const dialog = makeQuestionnaire(() => {});
    dialog.handleInput(DOWN);
    dialog.handleInput(ENTER); // Q1 → B（index=2），自动跳 Q2
    dialog.handleInput(LEFT); // 切回 Q1
    assert.equal((dialog as any).pages[0].optionIndex, 1, '光标落到已答的第 2 项');
  });

  it('自由项进编辑态：自定义答案 wasCustom=true 无 index', () => {
    const results: unknown[] = [];
    const dialog = makeQuestionnaire((r) => results.push(r));
    dialog.handleInput(DOWN);
    dialog.handleInput(DOWN); // Q1 的 "Type something."
    dialog.handleInput(ENTER);
    assert.equal((dialog as any).pages[0].editMode, true);
    (dialog as any).pages[0].editor.onSubmit('  自定义答案  ');
    assert.equal((dialog as any).currentPage, 1, '答完自动跳下一未答问');
    dialog.handleInput(ENTER); // Q2 → X
    dialog.handleInput(ENTER); // 提交
    assert.deepEqual(results, [
      {
        answers: [
          { answer: '自定义答案', wasCustom: true },
          { answer: 'X', wasCustom: false, index: 1 },
        ],
      },
    ]);
  });

  it('编辑态 esc 返回选项态；选项态 esc 取消 done(undefined)', () => {
    const results: unknown[] = [];
    const dialog = makeQuestionnaire((r) => results.push(r));
    dialog.handleInput(DOWN);
    dialog.handleInput(DOWN);
    dialog.handleInput(ENTER); // 编辑态
    dialog.handleInput(ESC); // 返回选项态
    assert.equal((dialog as any).pages[0].editMode, false);
    assert.deepEqual(results, []);
    dialog.handleInput(ESC); // 取消
    assert.deepEqual(results, [undefined]);
  });

  it('当前页渲染 = 单问页（问题 + 编号选项含自由项）', () => {
    const dialog = makeQuestionnaire(() => {});
    const text = dialog.render(60).join('\n');
    assert.ok(text.includes('Q1 选哪个？'));
    assert.ok(text.includes('1. A'));
    assert.ok(text.includes('3. Type something.'));
    assert.ok(text.includes('慢'));
  });
});

describe('questionDialogFactory（多问分派）', () => {
  it('questions 数组存在 → QuestionnaireDialog；否则单问 QuestionDialog', () => {
    const multi = questionDialogFactory(
      { tui: fakeTui },
      { questions: [{ question: 'q1', options: [{ label: 'A' }] }] },
      () => {},
    );
    assert.ok(multi instanceof QuestionnaireDialog);
    const single = questionDialogFactory(
      { tui: fakeTui },
      { question: 'q', options: [{ label: 'A' }] },
      () => {},
    );
    assert.ok(single instanceof QuestionDialog);
  });

  it('questions 空数组/全无效 → 落单问路径兜底', () => {
    const empty = questionDialogFactory({ tui: fakeTui }, { questions: [], question: 'q' }, () => {});
    assert.ok(empty instanceof QuestionDialog);
    const invalid = questionDialogFactory(
      { tui: fakeTui },
      { questions: ['bogus', { question: '' }], question: 'q' },
      () => {},
    );
    assert.ok(invalid instanceof QuestionDialog);
  });

  it('超过 4 问截断到 4 问', () => {
    const component = questionDialogFactory(
      { tui: fakeTui },
      {
        questions: Array.from({ length: 6 }, (_, i) => ({
          question: `q${i + 1}`,
          options: [{ label: 'A' }],
        })),
      },
      () => {},
    ) as QuestionnaireDialog;
    assert.equal((component as any).questions.length, 4);
  });

  it('工厂端到端：多问应答 {answers: [...]} 形状', () => {
    let captured: unknown;
    const component = questionDialogFactory(
      { tui: fakeTui },
      {
        questions: [
          { question: 'q1', options: [{ label: 'A' }, { label: 'B' }] },
          { question: 'q2', options: [{ label: 'X' }] },
        ],
      },
      (r) => (captured = r),
    ) as QuestionnaireDialog;
    component.handleInput(DOWN);
    component.handleInput(ENTER); // q1 → B
    component.handleInput(ENTER); // q2 → X
    component.handleInput(ENTER); // 提交
    assert.deepEqual(captured, {
      answers: [
        { answer: 'B', wasCustom: false, index: 2 },
        { answer: 'X', wasCustom: false, index: 1 },
      ],
    });
  });
});
