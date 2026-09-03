/**
 * interactive-shell 对话框测试（frontend/tui/dialogs/interactive-shell.ts——pi
 * interactive-shell.ts 对位）：挂起顺序 stop→清屏→spawn→start→done、真实
 * spawnSync 退出码透传、信号杀死（status=null）与 spawn 失败的 1 兜底、
 * 工厂不同步执行挂起周期（setImmediate 兼容 swap 时序）、占位组件提示行。
 *
 * mock tui 以事件序列记录 stop/start/requestRender；process.stdout.write 被
 * 短窗间谍吞掉清屏转义并记录 'clear'（同步窗口内替换与恢复，不影响测试输出）。
 * macOS 有 /bin/sh（SHELL 缺省兜底）；命令真实执行（exit 3 / kill -KILL）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  InteractiveShellDialog,
  interactiveShellDialogFactory,
  runInteractiveCommand,
  type InteractiveShellTui,
} from '../../tui/dialogs/interactive-shell.js';

function makeMockTui(events: string[]): InteractiveShellTui {
  return {
    stop: () => {
      events.push('stop');
    },
    start: () => {
      events.push('start');
    },
    requestRender: () => {
      events.push('render');
    },
  } as InteractiveShellTui;
}

/** 吞掉清屏转义并记录事件（其余块转发原样写出——不得吞 reporter 输出）。 */
function spyClearScreen(events: string[]): () => void {
  const original = process.stdout.write;
  process.stdout.write = ((chunk: unknown, ...rest: unknown[]) => {
    if (chunk === '\x1b[2J\x1b[H') {
      events.push('clear');
      return true;
    }
    return (original as (...args: unknown[]) => boolean).call(process.stdout, chunk, ...rest);
  }) as typeof process.stdout.write;
  return () => {
    process.stdout.write = original;
  };
}

describe('runInteractiveCommand', () => {
  it('挂起顺序 stop→clear→start→render→done，真实退出码 3 透传', () => {
    const events: string[] = [];
    const restore = spyClearScreen(events);
    let captured: unknown;
    try {
      runInteractiveCommand(makeMockTui(events), 'exit 3', '/tmp', (r) => {
        events.push('done');
        captured = r;
      });
    } finally {
      restore();
    }
    assert.deepEqual(captured, { exitCode: 3 });
    assert.deepEqual(events, ['stop', 'clear', 'start', 'render', 'done']);
  });

  it('信号杀死（status=null）兜底 exitCode=1', () => {
    const events: string[] = [];
    const restore = spyClearScreen(events);
    let captured: unknown;
    try {
      runInteractiveCommand(makeMockTui(events), 'kill -KILL $$', '/tmp', (r) => (captured = r));
    } finally {
      restore();
    }
    assert.deepEqual(captured, { exitCode: 1 });
  });

  it('spawn 失败（cwd 不存在，result.error）兜底 exitCode=1，仍恢复 TUI', () => {
    const events: string[] = [];
    const restore = spyClearScreen(events);
    let captured: unknown;
    try {
      runInteractiveCommand(
        makeMockTui(events),
        'true',
        '/nonexistent-dir-for-interactive-shell-test',
        (r) => {
          events.push('done');
          captured = r;
        },
      );
    } finally {
      restore();
    }
    assert.deepEqual(captured, { exitCode: 1 });
    assert.deepEqual(events, ['stop', 'clear', 'start', 'render', 'done'], '失败也要 start 恢复终端');
  });
});

describe('interactiveShellDialogFactory', () => {
  it('工厂不同步执行挂起周期：setImmediate 后 done 回执退出码', async () => {
    const events: string[] = [];
    const restore = spyClearScreen(events);
    let captured: unknown;
    let doneCalled = false;
    try {
      const component = interactiveShellDialogFactory(
        { tui: makeMockTui(events) },
        { command: 'exit 5', cwd: '/tmp' },
        (r) => {
          doneCalled = true;
          events.push('done');
          captured = r;
        },
      );
      assert.ok(
        typeof (component as { render?: unknown }).render === 'function',
        '工厂应返回占位组件',
      );
      assert.equal(doneCalled, false, '同步窗口内不得 done（restore/swap 顺序错乱陷阱）');
      assert.deepEqual(events, [], '同步窗口内不得启动挂起周期');
      // spawnSync 阻塞期间事件循环冻结——done 经 setImmediate 在 swap 之后
      await new Promise((resolve) => setImmediate(resolve));
    } finally {
      restore();
    }
    assert.equal(doneCalled, true);
    assert.deepEqual(captured, { exitCode: 5 });
    assert.deepEqual(events, ['stop', 'clear', 'start', 'render', 'done']);
  });

  it('参数缺失兜底：cwd 缺省 process.cwd()，空命令立即退出 0', async () => {
    const restore = spyClearScreen([]);
    let captured: unknown;
    try {
      interactiveShellDialogFactory({ tui: makeMockTui([]) }, {}, (r) => (captured = r));
      await new Promise((resolve) => setImmediate(resolve));
    } finally {
      restore();
    }
    assert.deepEqual(captured, { exitCode: 0 });
  });
});

describe('InteractiveShellDialog（占位组件）', () => {
  it('render 返回提示行', () => {
    const text = new InteractiveShellDialog().render(60).join('\n');
    assert.ok(text.includes('running interactive command…'));
  });
});
