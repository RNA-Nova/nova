/**
 * KeymapController 双 Esc 导航测试（pi 500ms 窗 + 设置档语义）。
 *
 * 轻 mock 注入（editor/runtime/dialogs 均为行为桩）；全局键位表经
 * NovaKeybindingsManager.create 路径注入隔离真实 ~/.nova。
 */

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, describe, it } from 'node:test';

import {
  KeybindingsManager,
  TUI_KEYBINDINGS,
  setKeybindings,
} from '@earendil-works/pi-tui';
import { SlotRegistry, shortcutSlot } from 'nova-tui';

import { KeymapController } from '../../../../src/modes/tui/controllers/keymap.js';
import { ForegroundTasks } from '../../../../src/modes/tui/controllers/foreground.js';
import { NovaKeybindingsManager } from '../../../../src/keymap/manager.js';
import { NOVA_KEYBINDINGS, RESERVED_KEYBINDINGS } from '../../../../src/modes/tui/keymap/tables.js';
import type { NovaUIRuntime } from 'nova-tui';
import type { ExpansionState } from '../../../../src/modes/tui/components/transcript/expansion.js';
import type { DialogController } from '../../../../src/modes/tui/controllers/dialogs.js';
import type { EditorController } from '../../../../src/modes/tui/controllers/editor.js';
import type { TranscriptController } from '../../../../src/modes/tui/controllers/transcript.js';

const ESC = '\x1b';

interface Harness {
  keymap: KeymapController;
  runtime: NovaUIRuntime;
  commands: string[];
  aborted: { run: number; retry: number; compaction: number };
  quits: number[];
  dequeued: number;
  thinkingCycled: number;
  modelCycled: string[];
  suspends: number;
  externalEditor: number;
  copied: number;
  infos: string[];
  foregroundTasks: ForegroundTasks;
  setText(text: string): void;
  setStatus(status: string): void;
  setAction(action: 'fork' | 'tree' | 'none'): void;
}

function makeHarness(): Harness {
  let text = '';
  let status = 'idle';
  let action: 'fork' | 'tree' | 'none' = 'tree';
  const harness: Harness = {
    runtime: undefined as never, // 下方装配后回填
    commands: [],
    aborted: { run: 0, retry: 0, compaction: 0 },
    quits: [],
    dequeued: 0,
    thinkingCycled: 0,
    modelCycled: [],
    suspends: 0,
    externalEditor: 0,
    copied: 0,
    infos: [],
    foregroundTasks: new ForegroundTasks(),
    setText: (value) => (text = value),
    setStatus: (value) => (status = value),
    setAction: (value) => (action = value),
  } as Harness;

  const editor = {
    getText: () => text,
    setText: (value: string) => (text = value),
  };
  const editorRef = { current: editor };
  const runtime = {
    store: { get status() { return status; } },
    slots: new SlotRegistry(),
    abort: async () => void harness.aborted.run++,
    abortRetry: async () => void harness.aborted.retry++,
    abortCompaction: async () => void harness.aborted.compaction++,
    cycleThinkingLevel: async () => void harness.thinkingCycled++,
    cycleModel: async (direction: string) => void harness.modelCycled.push(direction),
  } as unknown as NovaUIRuntime;
  harness.runtime = runtime;
  const dialogs = {
    isActive: false,
    hasAuthDialog: false,
  } as unknown as DialogController;
  const expansion: ExpansionState = { expanded: false };
  const transcript = {
    rebuildAll: () => {},
    copyLastAssistantMessage: async () => void harness.copied++,
    addInfo: (m: string) => void harness.infos.push(m),
  } as unknown as TranscriptController;
  const editorController = {
    runSlashCommand: (cmd: string) => void harness.commands.push(cmd),
    // 双 Esc 导航推命令（openNavigation → runCommand）；测试断言沿用 '/tree'/'/fork' 记录
    openNavigation: (action: 'tree' | 'fork') => void harness.commands.push(`/${action}`),
    // ctrl+l 推 '/model' 命令（bundle 包自持选择器，缺席走后端回退）
    runCommand: (name: string) => void harness.commands.push(`/${name}`),
    dequeueToEditor: async () => void harness.dequeued++,
  } as unknown as EditorController;

  harness.keymap = new KeymapController({
    editorRef: editorRef as never,
    runtime,
    dialogs,
    expansion,
    transcript,
    editorController,
    foregroundTasks: harness.foregroundTasks,
    doubleEscapeAction: () => action,
    toggleThinking: () => {},
    suspend: () => void harness.suspends++,
    openExternalEditor: () => void harness.externalEditor++,
    quit: (code) => void harness.quits.push(code),
  });
  return harness;
}

let dir: string;

before(() => {
  dir = mkdtempSync(join(tmpdir(), 'nova-keymap-esc-test-'));
  const { manager } = NovaKeybindingsManager.create(dir, {
    defaults: NOVA_KEYBINDINGS,
    reserved: RESERVED_KEYBINDINGS,
    paths: {
      user: join(dir, 'none-user.json'),
      project: join(dir, 'none-project.json'),
    },
  });
  setKeybindings(manager);
});

after(() => {
  setKeybindings(new KeybindingsManager(TUI_KEYBINDINGS)); // 恢复默认全局表
  rmSync(dir, { recursive: true, force: true });
});

describe('双 Esc 导航', () => {
  it('idle + 空编辑器：单击不触发，500ms 内双击触发 /tree（默认档）', () => {
    const h = makeHarness();
    assert.equal(h.keymap.handle(ESC), undefined); // 单击：记录时间，让路
    assert.deepEqual(h.commands, []);
    assert.equal(h.keymap.handle(ESC)?.consume, true); // 双击
    assert.deepEqual(h.commands, ['/tree']);
  });

  it('fork 档 → /fork；none 档 → 不触发', () => {
    const h = makeHarness();
    h.setAction('fork');
    h.keymap.handle(ESC);
    h.keymap.handle(ESC);
    assert.deepEqual(h.commands, ['/fork']);

    const h2 = makeHarness();
    h2.setAction('none');
    h2.keymap.handle(ESC);
    h2.keymap.handle(ESC);
    assert.deepEqual(h2.commands, []);
  });

  it('超窗（>500ms）重新计时', async () => {
    const h = makeHarness();
    h.keymap.handle(ESC);
    await new Promise((resolve) => setTimeout(resolve, 600));
    assert.equal(h.keymap.handle(ESC), undefined); // 超窗算单击
    assert.deepEqual(h.commands, []);
    assert.equal(h.keymap.handle(ESC)?.consume, true); // 紧接着的一次构成双击
    assert.deepEqual(h.commands, ['/tree']);
  });

  it('编辑器非空 → 双击也不导航', () => {
    const h = makeHarness();
    h.setText('hello');
    h.keymap.handle(ESC);
    h.keymap.handle(ESC);
    assert.deepEqual(h.commands, []);
  });

  it('working 状态 Esc → 先还原队列再 abort run（优先于导航）', async () => {
    const h = makeHarness();
    h.setStatus('working');
    assert.equal(h.keymap.handle(ESC)?.consume, true);
    await new Promise((resolve) => setImmediate(resolve)); // dequeue→abort 异步链
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(h.dequeued, 1); // 队列还原先于 abort
    assert.equal(h.aborted.run, 1);
    h.keymap.handle(ESC);
    assert.deepEqual(h.commands, []); // 无双击导航
  });

  it('retrying/compacting 状态 Esc → 域级 abort', () => {
    const h = makeHarness();
    h.setStatus('retrying');
    h.keymap.handle(ESC);
    assert.equal(h.aborted.retry, 1);
    h.setStatus('compacting');
    h.keymap.handle(ESC);
    assert.equal(h.aborted.compaction, 1);
  });

  it('双击触发后窗口重置（三连击只触发一次）', () => {
    const h = makeHarness();
    h.keymap.handle(ESC);
    h.keymap.handle(ESC); // 触发
    h.keymap.handle(ESC); // 窗口已重置——这是新的单击
    assert.deepEqual(h.commands, ['/tree']);
  });

  it('前台在飞任务：Esc 优先消费取消（不触发双 Esc 导航/域级 abort）', () => {
    const h = makeHarness();
    let cancelled = 0;
    const unregister = h.foregroundTasks.register(() => cancelled++);
    assert.equal(h.keymap.handle(ESC)?.consume, true); // Esc 被前台任务消费
    assert.equal(cancelled, 1);
    assert.deepEqual(h.commands, []); // 不构成双击导航的单击记录
    assert.equal(h.aborted.run, 0);
    unregister(); // 幂等（已消费——注销不再触发）
    h.keymap.handle(ESC);
    assert.equal(cancelled, 1); // 第二次 Esc 不再命中（登记处已空）
  });

  it('shift+tab：循环 thinking 级别（后端 cycleThinkingLevel）', async () => {
    const h = makeHarness();
    h.keymap.handle('\x1b[Z'); // shift+tab 反向制表序列
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(h.thinkingCycled, 1);
  });

  it('ctrl+p：模型循环 forward（后端 cycleModel）', async () => {
    const h = makeHarness();
    h.keymap.handle('\x10'); // ctrl+p
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(h.modelCycled, ['forward']);
  });

  it('ctrl+l：推 /model 命令（bundle 包自持选择器，缺席走后端回退）', () => {
    const h = makeHarness();
    assert.equal(h.keymap.handle('\x0c')?.consume, true); // ctrl+l
    assert.deepEqual(h.commands, ['/model']);
  });

  it('ctrl+x：复制最后回复 / ctrl+z：挂起 / ctrl+g：外部编辑器', () => {
    const h = makeHarness();
    assert.equal(h.keymap.handle('\x18')?.consume, true); // ctrl+x
    assert.equal(h.copied, 1);
    if (process.platform === 'win32') {
      // ctrl+z 挂起在 Windows 键位表中刻意禁用（无 POSIX 挂起语义）——
      // 按键让路不消费
      assert.equal(h.keymap.handle('\x1a'), undefined);
      assert.equal(h.suspends, 0);
    } else {
      assert.equal(h.keymap.handle('\x1a')?.consume, true); // ctrl+z
      assert.equal(h.suspends, 1);
    }
    assert.equal(h.keymap.handle('\x07')?.consume, true); // ctrl+g
    assert.equal(h.externalEditor, 1);
  });

  it('扩展快捷键：对账后命中执行（先于内建路由）', async () => {
    const h = makeHarness();
    let fired = 0;
    h.runtime.slots.register(
      shortcutSlot('ctrl+u'),
      () => void fired++,
      'test-pkg',
    );
    h.keymap.validateExtensionShortcuts();
    assert.equal(h.keymap.handle('\x15')?.consume, true); // ctrl+u
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(fired, 1);
  });

  it('restrictOverride：撞保留键位的扩展快捷键禁用 + 诊断', () => {
    const h = makeHarness();
    let fired = 0;
    h.runtime.slots.register(
      shortcutSlot('escape'), // app.interrupt 的当前绑定键（保留）
      () => void fired++,
      'test-pkg',
    );
    h.keymap.validateExtensionShortcuts();
    assert.match(h.infos[0] ?? '', /撞保留键位/);
    h.keymap.handle('\x1b'); // escape——扩展快捷键已被剔除，走 Esc 域级路由
    assert.equal(fired, 0);
  });
});

describe('kitty 键盘协议事件类型过滤（VS Code 终端回归）', () => {
  // kitty CSI-u 事件类型：press 无后缀 / repeat=:2 / release=:3
  const CTRL_C_PRESS = '\x1b[99;5u';
  const CTRL_C_REPEAT = '\x1b[99;5:2u';
  const CTRL_C_RELEASE = '\x1b[99;5:3u';

  it('release 事件不算按下（单击 ctrl+c 不再误退）', () => {
    const h = makeHarness();
    // 一次物理 ctrl+c = press + release 两个事件：release 不得构成第二次按下
    h.keymap.handle(CTRL_C_PRESS);
    h.keymap.handle(CTRL_C_RELEASE);
    assert.equal(h.quits.length, 0); // 若 release 被计数，这里已退出

    // 单独的 release（无 press 配对）直接让路，无任何副作用
    assert.equal(h.keymap.handle(CTRL_C_RELEASE), undefined);
    assert.equal(h.quits.length, 0);
  });

  it('repeat 事件不累计双击窗口（长按 ctrl+c 不退出）', () => {
    const h = makeHarness();
    h.keymap.handle(CTRL_C_PRESS);
    h.keymap.handle(CTRL_C_REPEAT);
    h.keymap.handle(CTRL_C_REPEAT);
    assert.equal(h.quits.length, 0);
  });

  it('对照组：两次真实 press 仍触发双击退出', () => {
    const h = makeHarness();
    h.keymap.handle(CTRL_C_PRESS);
    h.keymap.handle(CTRL_C_PRESS);
    assert.equal(h.quits.length, 1);
  });
});
