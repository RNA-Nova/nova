/**
 * ToolsDialog 组件测试（frontend/tui/dialogs/tools.ts——pi tools.ts 的
 * SettingsList 对位）：初始激活态渲染、space 切换、enter 提交 {active: [...]}
 * （入参原序）、esc 取消、工厂参数归一化。colors 经 env 注入恒等函数（无 ANSI）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { ToolsDialog, toolsDialogFactory } from '../../tui/dialogs/tools.js';

const identity = (s: string) => s;
const colors = new Proxy({}, { get: () => identity }) as Record<string, (s: string) => string>;

// 键位以终端转义序列驱动（与真实输入同形态）
const DOWN = '\x1b[B';
const UP = '\x1b[A';
const ENTER = '\r';
const ESC = '\x1b';
const SPACE = ' ';

const SAMPLE_TOOLS = [
  { name: 'bash', label: 'Bash', description: '执行 shell 命令', active: true },
  { name: 'edit', label: 'Edit', description: '编辑文件', active: false },
  { name: 'grep', label: 'Grep', description: '搜索内容', active: true },
];

function makeDialog(
  done: (result?: { active: string[] }) => void,
  tools = SAMPLE_TOOLS,
) {
  return new ToolsDialog(tools, colors as never, done);
}

describe('ToolsDialog', () => {
  it('初始渲染：激活 [x] / 未激活 [ ]，label — description 行', () => {
    const dialog = makeDialog(() => {});
    const text = dialog.render(70).join('\n');
    assert.ok(text.includes('[x] Bash — 执行 shell 命令'));
    assert.ok(text.includes('[ ] Edit — 编辑文件'));
    assert.ok(text.includes('[x] Grep — 搜索内容'));
    assert.ok(text.includes('激活 2/3'));
  });

  it('↑↓ 移动光标行', () => {
    const dialog = makeDialog(() => {});
    assert.equal((dialog as any).selectedIndex, 0);
    dialog.handleInput(DOWN);
    assert.equal((dialog as any).selectedIndex, 1);
    dialog.handleInput(DOWN);
    dialog.handleInput(DOWN); // 越界钳制
    assert.equal((dialog as any).selectedIndex, 2);
    dialog.handleInput(UP);
    assert.equal((dialog as any).selectedIndex, 1);
  });

  it('space 切换激活态（渲染随之翻转）', () => {
    const dialog = makeDialog(() => {});
    dialog.handleInput(SPACE); // bash 关
    let text = dialog.render(70).join('\n');
    assert.ok(text.includes('[ ] Bash'));
    dialog.handleInput(DOWN);
    dialog.handleInput(SPACE); // edit 开
    text = dialog.render(70).join('\n');
    assert.ok(text.includes('[x] Edit'));
    assert.ok(text.includes('激活 2/3'));
  });

  it('enter 提交 {active: [name...]}（按入参原序归集）', () => {
    const results: unknown[] = [];
    const dialog = makeDialog((r) => results.push(r));
    dialog.handleInput(SPACE); // bash 关
    dialog.handleInput(DOWN);
    dialog.handleInput(SPACE); // edit 开
    dialog.handleInput(ENTER);
    assert.deepEqual(results, [{ active: ['edit', 'grep'] }]);
  });

  it('无改动 enter：提交初始激活集', () => {
    const results: unknown[] = [];
    const dialog = makeDialog((r) => results.push(r));
    dialog.handleInput(ENTER);
    assert.deepEqual(results, [{ active: ['bash', 'grep'] }]);
  });

  it('esc 取消：done(undefined)', () => {
    const results: unknown[] = [];
    const dialog = makeDialog((r) => results.push(r));
    dialog.handleInput(ESC);
    assert.deepEqual(results, [undefined]);
  });
});

describe('toolsDialogFactory', () => {
  it('参数归一化：非对象/无 name 项过滤，label 缺省回退 name', () => {
    let captured: unknown;
    const component = toolsDialogFactory(
      { colors },
      {
        tools: [
          { name: 'bash', active: true },
          'bogus',
          { label: '无 name' },
          { name: 'edit', label: 'Edit', description: '编辑', active: 1 }, // 非布尔 active → false
        ],
      },
      (r) => (captured = r),
    ) as ToolsDialog;
    assert.equal((component as any).tools.length, 2, '非法项应被过滤');
    const text = component.render(60).join('\n');
    assert.ok(text.includes('[x] bash'), 'label 缺省回退 name');
    component.handleInput(ENTER);
    assert.deepEqual(captured, { active: ['bash'] });
  });

  it('缺失参数不炸：空面板渲染 + enter 提交空集', () => {
    let captured: unknown;
    const component = toolsDialogFactory({ colors }, {}, (r) => (captured = r)) as ToolsDialog;
    const text = component.render(50).join('\n');
    assert.ok(text.includes('无可用工具'));
    component.handleInput(ENTER);
    assert.deepEqual(captured, { active: [] });
  });
});
