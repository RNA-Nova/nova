/**
 * AdjudicationDialog 组件测试（frontend/tui/dialogs/adjudication.ts——
 * 裁决/审批流专用框）：选项表三态组装（proposeAmendment 有无 forever）、
 * 渲染（标题/命令块/原因/选项热键）、↑↓ 移动、enter 提交选中决策、
 * y/s/a/n 热键直达、esc 取消。colors 经 env 注入恒等函数（无 ANSI）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  AdjudicationDialog,
  adjudicationDialogFactory,
  adjudicationOptions,
} from '../../tui/dialogs/adjudication.js';

const identity = (s: string) => s;
const colors = new Proxy({}, { get: () => identity }) as Record<string, (s: string) => string>;

// 键位以终端转义序列驱动（与真实输入同形态）
const DOWN = '\x1b[B';
const UP = '\x1b[A';
const ENTER = '\r';
const ESC = '\x1b';

function makeDialog(
  done: (result?: { decision: string }) => void,
  options = adjudicationOptions(true),
) {
  return new AdjudicationDialog('执行 bash 命令', 'rm -rf /tmp/x', '规则要求确认', options, colors as never, done);
}

describe('adjudicationOptions', () => {
  it('proposeAmendment=true → 四选项（含永远允许）', () => {
    const options = adjudicationOptions(true);
    assert.deepEqual(
      options.map((o) => o.decision),
      ['once', 'session', 'forever', 'deny'],
    );
    assert.deepEqual(
      options.map((o) => o.hotkey),
      ['y', 's', 'a', 'n'],
    );
  });

  it('proposeAmendment=false → 三选项（无永远允许）', () => {
    const options = adjudicationOptions(false);
    assert.deepEqual(
      options.map((o) => o.decision),
      ['once', 'session', 'deny'],
    );
  });
});

describe('AdjudicationDialog', () => {
  it('初始渲染：标题 / 命令块 / 原因 / 选项与热键', () => {
    const dialog = makeDialog(() => {});
    const text = dialog.render(70).join('\n');
    assert.ok(text.includes('⚠ 执行 bash 命令'));
    assert.ok(text.includes('rm -rf /tmp/x'));
    assert.ok(text.includes('原因: 规则要求确认'));
    assert.ok(text.includes('允许一次  (y)'));
    assert.ok(text.includes('本会话都允许  (s)'));
    assert.ok(text.includes('永远允许（写入规则）  (a)'));
    assert.ok(text.includes('拒绝  (n)'));
  });

  it('命令/原因缺席时不渲染对应段', () => {
    const dialog = new AdjudicationDialog('仅标题', null, null, adjudicationOptions(false), colors as never, () => {});
    const text = dialog.render(70).join('\n');
    assert.ok(text.includes('⚠ 仅标题'));
    assert.ok(!text.includes('原因:'));
    assert.ok(!text.includes('(a)'));
  });

  it('↑↓ 移动光标行（越界钳制）', () => {
    const dialog = makeDialog(() => {});
    assert.equal((dialog as any).selectedIndex, 0);
    dialog.handleInput(DOWN);
    assert.equal((dialog as any).selectedIndex, 1);
    dialog.handleInput(DOWN);
    dialog.handleInput(DOWN);
    dialog.handleInput(DOWN); // 越界钳制在末项
    assert.equal((dialog as any).selectedIndex, 3);
    dialog.handleInput(UP);
    assert.equal((dialog as any).selectedIndex, 2);
  });

  it('enter 提交光标行决策', () => {
    const results: Array<{ decision: string } | undefined> = [];
    const dialog = makeDialog((r) => results.push(r));
    dialog.handleInput(DOWN);
    dialog.handleInput(ENTER);
    assert.deepEqual(results, [{ decision: 'session' }]);
  });

  it('热键直达提交对应决策', () => {
    for (const [key, decision] of [
      ['y', 'once'],
      ['s', 'session'],
      ['a', 'forever'],
      ['n', 'deny'],
    ] as const) {
      const results: Array<{ decision: string } | undefined> = [];
      makeDialog((r) => results.push(r)).handleInput(key);
      assert.deepEqual(results, [{ decision }]);
    }
  });

  it('esc 取消（undefined——后端按拒绝处理）', () => {
    const results: Array<{ decision: string } | undefined> = [];
    makeDialog((r) => results.push(r)).handleInput(ESC);
    assert.deepEqual(results, [undefined]);
  });
});

describe('adjudicationDialogFactory', () => {
  it('params 归一化：title/command/reason/proposeAmendment', () => {
    let built: AdjudicationDialog | null = null;
    const done = () => {};
    built = adjudicationDialogFactory(
      { colors },
      { title: 'T', command: 'cmd', reason: 'R', proposeAmendment: true },
      done,
    ) as AdjudicationDialog;
    assert.equal((built as any).options.length, 4);
    built = adjudicationDialogFactory({ colors }, { title: 'T' }, done) as AdjudicationDialog;
    assert.equal((built as any).options.length, 3);
    // 非字符串/缺省 → 兜底文案
    built = adjudicationDialogFactory({ colors }, {}, done) as AdjudicationDialog;
    const text = built.render(60).join('\n');
    assert.ok(text.includes('⚠ 审批请求'));
  });
});
