/**
 * 信号守卫测试：注入 fake 进程面（EventEmitter + 记录型 exit/printError），
 * 验证行为矩阵——优雅关闭顺序/死终端 129/崩溃恢复/幂等/卸载。
 */

import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { describe, it } from 'node:test';

import { installSignalHandlers, type SignalHandlerDeps } from '../../../../src/modes/tui/utils/signals.js';

interface FakeProc {
  proc: NodeJS.Process;
  stdout: EventEmitter;
  stderr: EventEmitter;
}

function createFakeProc(): FakeProc {
  const stdout = new EventEmitter();
  const stderr = new EventEmitter();
  const proc = new EventEmitter() as unknown as NodeJS.Process;
  proc.stdout = stdout as unknown as NodeJS.Process['stdout'];
  proc.stderr = stderr as unknown as NodeJS.Process['stderr'];
  return { proc, stdout, stderr };
}

interface Fixture {
  proc: NodeJS.Process;
  stdout: EventEmitter;
  stderr: EventEmitter;
  /** 调用顺序记录（runtime.stop/tui.stop/quit/exit）。 */
  calls: string[];
  exitCodes: number[];
  printed: string[];
  uninstall: () => void;
}

function createFixture(): Fixture {
  const { proc, stdout, stderr } = createFakeProc();
  const calls: string[] = [];
  const exitCodes: number[] = [];
  const printed: string[] = [];
  const deps: SignalHandlerDeps = {
    runtime: {
      stop: async () => {
        calls.push('runtime.stop');
      },
    },
    tui: {
      stop: () => {
        calls.push('tui.stop');
      },
    },
    quit: (code) => {
      calls.push(`quit(${code})`);
    },
  };
  const uninstall = installSignalHandlers(deps, {
    proc,
    exit: (code) => {
      calls.push(`exit(${code})`);
      exitCodes.push(code);
    },
    printError: (text) => {
      printed.push(text);
    },
  });
  return { proc, stdout, stderr, calls, exitCodes, printed, uninstall };
}

/** 等 fire-and-forget 的优雅关闭跑完（runtime.stop 的 await 链）。 */
async function flush(): Promise<void> {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

function errnoError(code: string): NodeJS.ErrnoException {
  const error = new Error(`fake ${code}`) as NodeJS.ErrnoException;
  error.code = code;
  return error;
}

describe('installSignalHandlers · 信号优雅关闭', () => {
  it('SIGTERM → runtime.stop → tui.stop → exit(0)（顺序保证）', async () => {
    const fixture = createFixture();
    fixture.proc.emit('SIGTERM');
    await flush();
    assert.deepEqual(fixture.calls, ['runtime.stop', 'tui.stop', 'exit(0)']);
    fixture.uninstall();
  });

  it('SIGHUP → 同款优雅关闭（非 Windows 注册）', async () => {
    const fixture = createFixture();
    fixture.proc.emit('SIGHUP');
    await flush();
    assert.deepEqual(fixture.calls, ['runtime.stop', 'tui.stop', 'exit(0)']);
    fixture.uninstall();
  });

  it('SIGINT → quit(0) 交互式退出路径（不直接停后端）', () => {
    const fixture = createFixture();
    fixture.proc.emit('SIGINT');
    assert.deepEqual(fixture.calls, ['quit(0)']);
    fixture.uninstall();
  });

  it('重复信号幂等：只关一次', async () => {
    const fixture = createFixture();
    fixture.proc.emit('SIGTERM');
    fixture.proc.emit('SIGTERM');
    await flush();
    assert.deepEqual(
      fixture.calls.filter((call) => call === 'runtime.stop').length,
      1,
    );
    fixture.uninstall();
  });

  it('runtime.stop 抛错也继续收尾（后端可能已死）', async () => {
    const { proc } = createFakeProc();
    const calls: string[] = [];
    const uninstall = installSignalHandlers(
      {
        runtime: {
          stop: async () => {
            calls.push('runtime.stop');
            throw new Error('backend dead');
          },
        },
        tui: {
          stop: () => {
            calls.push('tui.stop');
          },
        },
        quit: () => undefined,
      },
      {
        proc,
        exit: (code) => {
          calls.push(`exit(${code})`);
        },
        printError: () => undefined,
      },
    );
    proc.emit('SIGTERM');
    await flush();
    assert.deepEqual(calls, ['runtime.stop', 'tui.stop', 'exit(0)']);
    uninstall();
  });
});

describe('installSignalHandlers · 死终端守卫', () => {
  it('stdout EIO → 应急 exit(129)，不做终端恢复', () => {
    const fixture = createFixture();
    fixture.stdout.emit('error', errnoError('EIO'));
    assert.deepEqual(fixture.calls, ['exit(129)']);
  });

  it('stderr ENOTCONN → 应急 exit(129)', () => {
    const fixture = createFixture();
    fixture.stderr.emit('error', errnoError('ENOTCONN'));
    assert.deepEqual(fixture.calls, ['exit(129)']);
  });

  it('非死终端错误重抛（转 uncaughtException 路径）', () => {
    const fixture = createFixture();
    assert.throws(() => fixture.stdout.emit('error', errnoError('ENOENT')), /fake ENOENT/);
    assert.deepEqual(fixture.calls, []);
    fixture.uninstall();
  });
});

describe('installSignalHandlers · 崩溃恢复', () => {
  it('uncaughtException → tui.stop 恢复终端 → 打印堆栈 → exit(1)', () => {
    const fixture = createFixture();
    fixture.proc.emit('uncaughtException', new Error('kaboom'));
    assert.deepEqual(fixture.calls, ['tui.stop', 'exit(1)']);
    assert.equal(fixture.printed[0], 'nova 因未捕获异常退出：');
    assert.match(fixture.printed[1] ?? '', /kaboom/);
  });

  it('unhandledRejection → 同款收尾', () => {
    const fixture = createFixture();
    fixture.proc.emit('unhandledRejection', new Error('reject boom'));
    assert.deepEqual(fixture.calls, ['tui.stop', 'exit(1)']);
    assert.equal(fixture.printed[0], 'nova 因未处理的 Promise rejection退出：');
    assert.match(fixture.printed[1] ?? '', /reject boom/);
  });

  it('关闭流程中的崩溃 → 直接 exit(1)（不再恢复终端）', async () => {
    const fixture = createFixture();
    fixture.proc.emit('SIGTERM');
    await flush();
    fixture.proc.emit('uncaughtException', new Error('late'));
    assert.deepEqual(fixture.exitCodes, [0, 1]);
    // tui.stop 只在优雅路径调过一次
    assert.deepEqual(
      fixture.calls.filter((call) => call === 'tui.stop').length,
      1,
    );
  });

  it('tui.stop 抛错也继续退出', () => {
    const { proc } = createFakeProc();
    const exitCodes: number[] = [];
    const uninstall = installSignalHandlers(
      {
        runtime: { stop: async () => undefined },
        tui: {
          stop: () => {
            throw new Error('restore fail');
          },
        },
        quit: () => undefined,
      },
      {
        proc,
        exit: (code) => {
          exitCodes.push(code);
        },
        printError: () => undefined,
      },
    );
    proc.emit('uncaughtException', new Error('x'));
    assert.deepEqual(exitCodes, [1]);
    uninstall();
  });
});

describe('installSignalHandlers · 卸载', () => {
  it('uninstall 移除全部监听器', () => {
    const fixture = createFixture();
    fixture.uninstall();
    for (const event of ['SIGINT', 'SIGTERM', 'SIGHUP', 'uncaughtException', 'unhandledRejection']) {
      assert.equal(fixture.proc.listenerCount(event), 0, event);
    }
    assert.equal(fixture.stdout.listenerCount('error'), 0);
    assert.equal(fixture.stderr.listenerCount('error'), 0);
  });

  it('卸载后信号不再触发退出', async () => {
    const fixture = createFixture();
    fixture.uninstall();
    fixture.proc.emit('SIGTERM');
    fixture.proc.emit('SIGINT');
    await flush();
    assert.deepEqual(fixture.calls, []);
  });
});
