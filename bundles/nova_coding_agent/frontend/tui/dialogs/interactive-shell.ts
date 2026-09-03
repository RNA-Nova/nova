/**
 * interactive-shell 终端让位对话框（dialog:interactive-shell——pi
 * interactive-shell.ts 对位）：挂起 TUI、清屏、以继承 stdio 同步执行交互
 * 命令、恢复 TUI 并回执退出码。
 *
 * 契约：
 * - 入参 params：{ command: string, cwd: string }；
 * - done({exitCode})：命令退出码（被信号杀死 status=null 或 spawn 失败按 1）。
 *
 * 陷阱：DialogController 先同步调工厂、之后才 swap 挂载——工厂内同步执行
 * 挂起周期（含同步 done）会导致 restore/swap 顺序错乱卡死。故工厂只返回
 * 占位组件，挂起周期经 setImmediate 异步启动（nova-client app.ts 的
 * openExternalEditor 同为 stop→外部进程→start→requestRender 序）。
 */
import { spawnSync } from 'node:child_process';

import type { Component, TUI } from '@earendil-works/pi-tui';

import { colors } from 'nova-tui/modes/tui/themes/index';

/** 挂起周期消费的 TUI 面（窄接口——测试以计数 mock 驱动）。 */
export type InteractiveShellTui = Pick<TUI, 'stop' | 'start' | 'requestRender'>;

/**
 * 挂起周期（pi interactive-shell + nova-client openExternalEditor 同序）：
 * tui.stop() 让位终端 → 清屏 → spawnSync(shell -c command, stdio 继承) →
 * tui.start() + requestRender(true) 恢复 → done({exitCode})。
 */
export function runInteractiveCommand(
  tui: InteractiveShellTui,
  command: string,
  cwd: string,
  done: (result: { exitCode: number }) => void,
): void {
  tui.stop(); // 让位终端
  process.stdout.write('\x1b[2J\x1b[H'); // 清屏，把完整终端交给交互命令
  const result = spawnSync(process.env.SHELL || '/bin/sh', ['-c', command], {
    stdio: 'inherit',
    cwd,
  });
  tui.start();
  tui.requestRender(true);
  // status 被信号杀死为 null（?? 1）；spawn 本身失败（error，如 cwd 不存在）也按 1
  const exitCode = result.error ? 1 : (result.status ?? 1);
  done({ exitCode });
}

/** 占位组件（挂起周期执行期间编辑器槽位的提示行）。 */
export class InteractiveShellDialog implements Component {
  render(_width: number): string[] {
    return [colors.dim('running interactive command…')];
  }

  invalidate(): void {
    // 无内部缓存——静态提示行
  }
}

/** dialog:interactive-shell 工厂（ExtensionUIAPI.registerDialog 的注册形态）。 */
export function interactiveShellDialogFactory(
  env: unknown,
  params: Record<string, unknown>,
  done: (result?: unknown) => void,
): Component {
  const { tui } = env as { tui: InteractiveShellTui };
  const command = typeof params.command === 'string' ? params.command : '';
  const cwd = typeof params.cwd === 'string' ? params.cwd : process.cwd();
  // 挂起周期不得同步执行（见文件头陷阱说明）——setImmediate 推迟到 swap 之后
  setImmediate(() => {
    runInteractiveCommand(tui, command, cwd, (result) => done(result));
  });
  return new InteractiveShellDialog();
}
