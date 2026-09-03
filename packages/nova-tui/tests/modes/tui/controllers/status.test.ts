/** StatusController 的 working 三旋钮（pi setWorking* 对位）测试。 */

import assert from 'node:assert/strict';
import { after, afterEach, describe, it } from 'node:test';

import { NovaUIRuntime } from 'nova-tui';
import { Container } from '@earendil-works/pi-tui';

import { StatusController } from '../../../../src/modes/tui/controllers/status.js';

function make() {
  const runtime = new NovaUIRuntime();
  const tui = { requestRender() {}, requestRenderFull() {} } as never;
  const container = new Container();
  const controller = new StatusController(tui, container, runtime);
  live.push(controller);
  return { runtime, tui, container, controller };
}

function forceWorking(runtime: NovaUIRuntime): void {
  // agent_start 事件直写 status = working（mirror 归约同路径）
  runtime.store.apply({ type: 'agent_start' } as never);
}

let live: StatusController[] = [];
afterEach(() => {
  // pi-tui Loader 的动画 setInterval 未 unref——用例间必须 dispose 防进程挂死
  for (const c of live) (c as never as { indicator?: { dispose(): void } }).indicator?.dispose();
  live = [];
});

describe('StatusController · working 三旋钮', () => {
  it('默认：working 态出现 Working… 指示器', () => {
    const { runtime, controller } = make();
    forceWorking(runtime);
    controller.refresh();
    const indicator = (controller as never as { indicator?: { kind: string } }).indicator;
    assert.equal(indicator?.kind, 'working');
  });

  it('setWorkingVisible(false)：working 态不建指示器', () => {
    const { runtime, controller } = make();
    controller.setWorkingVisible(false);
    forceWorking(runtime);
    controller.refresh();
    const indicator = (controller as never as { indicator?: unknown }).indicator;
    assert.equal(indicator, undefined);
  });

  it('setWorkingMessage / setWorkingIndicator：文案与帧透传进 Loader', () => {
    const { runtime, controller } = make();
    controller.setWorkingMessage('奔跑中…');
    controller.setWorkingIndicator({ frames: ['◐', '◓'], intervalMs: 50 });
    forceWorking(runtime);
    controller.refresh();
    const indicator = (controller as never as {
      indicator?: { kind: string; render: (w: number) => string[] };
    }).indicator;
    assert.equal(indicator?.kind, 'working');
    assert.match(indicator!.render(60).join('\n'), /奔跑中…/);
  });

  it('旋钮变更即重建（同变体续用路径被绕过）', () => {
    const { runtime, controller } = make();
    forceWorking(runtime);
    controller.refresh();
    const first = (controller as never as { indicator?: unknown }).indicator;
    controller.setWorkingMessage('新文案');
    const second = (controller as never as { indicator?: unknown }).indicator;
    assert.notEqual(first, second);
  });
});

after(() => {
  // 测试环境退出保障：本文件用 mock TUI 驱动真实 Loader——用例已逐一 dispose
  // （interval 已清、handles 为空），但进程仍被两个悬置的 PipeConnectWrap
  // 请求挂在事件循环（node 层工件，非产品逻辑——生产 TUI 经 quit() 退出）。
  // node --test 每文件独立进程，这里显式退出不影响其他文件。
  process.exit(0);
});
