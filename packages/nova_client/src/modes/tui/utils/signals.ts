/**
 * 信号与崩溃防护（pi interactive-mode.ts:3525-3610 registerSignalHandlers 对位）。
 *
 * 行为矩阵：
 * - **SIGINT** → `quit(0)`（交互式退出路径——与 ctrl-c 双击/ctrl-d 同路，
 *   由装配方决定打印恢复提示等收尾）；
 * - **SIGTERM/SIGHUP** → 优雅关闭：`runtime.stop()`（容错——后端可能已死）
 *   → `tui.stop()` 恢复终端 → `exit(0)`（pi shutdown({fromSignal}) 对位；
 *   SIGHUP 不再硬退——终端真死了会由下面的 EIO 守卫转成 129）；
 * - **stdout/stderr EIO/EPIPE/ENOTCONN** → 死终端应急 `exit(129)`——
 *   终端已死，任何恢复写都会再触发 EIO，不做终端恢复；
 * - **uncaughtException / unhandledRejection** → `tui.stop()` 恢复
 *   cooked 模式/光标 → 打印堆栈 → `exit(1)`（防 raw 模式残留——
 *   否则用户得 `stty sane && reset` 才能捡回终端）。
 *
 * 与 utils/terminal-guard.ts 的差异：pi 对齐版——信号先停后端再恢复终端
 * （原实现 quit 内 fire-and-forget）、覆盖 unhandledRejection、可卸载、
 * 进程面可注入（测试友好）。app.ts 装配后替换 setupTerminalGuards 调用。
 */

import { isDeadTerminalError } from './terminal-guard.js';

export interface SignalHandlerDeps {
  /** 后端运行时（stop 容错——后端进程可能已死）。 */
  runtime: { stop(): Promise<unknown> };
  /** TUI（stop 恢复终端 cooked 模式/光标/括号粘贴）。 */
  tui: { stop(): void };
  /** 交互式退出（SIGINT 走此——与 ctrl-c 双击/ctrl-d 同路径）。 */
  quit: (code: number) => void;
}

/** 可注入的进程面（测试注入 fake；缺省真实 process/console）。 */
export interface SignalEnv {
  proc: NodeJS.Process;
  exit: (code: number) => void;
  printError: (text: string) => void;
}

/**
 * 注册全部信号守卫；返回卸载函数（crash/应急路径自卸，测试收尾用）。
 * 对齐 pi 用 prependListener——保证先于其他监听器（如 signal-exit）执行。
 */
export function installSignalHandlers(
  deps: SignalHandlerDeps,
  env?: Partial<SignalEnv>,
): () => void {
  const proc = env?.proc ?? process;
  const exit = env?.exit ?? ((code: number) => process.exit(code));
  const printError = env?.printError ?? ((text: string) => console.error(text));

  let shuttingDown = false;
  const cleanups: Array<() => void> = [];
  const uninstall = (): void => {
    for (const cleanup of cleanups.splice(0)) cleanup();
  };

  /** 死终端应急退出：不做恢复写（终端已死——写序列会再触发 EIO）。 */
  const emergencyTerminalExit = (): void => {
    shuttingDown = true;
    uninstall();
    exit(129);
  };

  /** 崩溃收尾：终端还活着——先恢复 cooked/光标，再打印堆栈退出。 */
  const crashExit = (label: string, error: unknown): void => {
    if (shuttingDown) {
      exit(1);
      return;
    }
    shuttingDown = true;
    try {
      uninstall();
    } catch {
      // 卸载失败也继续退出
    }
    try {
      deps.tui.stop();
    } catch {
      // 恢复失败也继续退出
    }
    printError(`nova 因${label}退出：`);
    printError(error instanceof Error ? (error.stack ?? error.message) : String(error));
    exit(1);
  };

  /**
   * 信号优雅关闭（pi shutdown({fromSignal}) 对位）：先停后端再恢复终端。
   * 关闭期间保持处理器注册（pi 同款——signal-exit 会在同一派发窗口
   * 检查监听器列表，提前卸载会导致信号被重发）。
   */
  const gracefulShutdown = async (): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    try {
      await deps.runtime.stop();
    } catch {
      // 后端已死也继续收尾
    }
    try {
      deps.tui.stop();
    } catch {
      // 恢复失败也继续退出
    }
    exit(0);
  };

  // SIGINT → 交互式退出路径（quit 内部自管 runtime/tui 收尾与恢复提示）
  const onSigint = (): void => deps.quit(0);
  proc.prependListener('SIGINT', onSigint);
  cleanups.push(() => proc.off('SIGINT', onSigint));

  // SIGTERM/SIGHUP → 优雅关闭（SIGHUP 仅非 Windows——pi 同款守卫）
  const gracefulSignals: NodeJS.Signals[] = ['SIGTERM'];
  if (process.platform !== 'win32') gracefulSignals.push('SIGHUP');
  for (const signal of gracefulSignals) {
    const handler = (): void => {
      void gracefulShutdown();
    };
    proc.prependListener(signal, handler);
    cleanups.push(() => proc.off(signal, handler));
  }

  // 死终端守卫：EIO/EPIPE/ENOTCONN → 129；其余错误重抛（转 uncaughtException 路径）
  const terminalErrorHandler = (error: Error): void => {
    if (isDeadTerminalError(error)) {
      emergencyTerminalExit();
      return;
    }
    throw error;
  };
  proc.stdout.on('error', terminalErrorHandler);
  proc.stderr.on('error', terminalErrorHandler);
  cleanups.push(() => proc.stdout.off('error', terminalErrorHandler));
  cleanups.push(() => proc.stderr.off('error', terminalErrorHandler));

  // 未捕获异常 / 未处理 rejection：恢复终端后打印堆栈退出
  const onUncaughtException = (error: Error): void => crashExit('未捕获异常', error);
  proc.prependListener('uncaughtException', onUncaughtException);
  cleanups.push(() => proc.off('uncaughtException', onUncaughtException));

  const onUnhandledRejection = (reason: unknown): void =>
    crashExit('未处理的 Promise rejection', reason);
  proc.prependListener('unhandledRejection', onUnhandledRejection);
  cleanups.push(() => proc.off('unhandledRejection', onUnhandledRejection));

  return uninstall;
}
