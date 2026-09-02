/**
 * ToolCardView 指纹 memo + ElapsedLine 计时行测试（pi 对齐改造后）：
 * - 指纹不变的 update 不重建（渲染器零重调——消"每事件重建所有卡片"放大器）；
 * - live 卡片挂计时行（宿主 chrome，pi Loader 自转对位）；
 * - 完结/dispose 停表（interval 不泄漏）。真实定时器（非 mock）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { Text } from '@earendil-works/pi-tui';

import { ToolCardView } from '../../../../../src/modes/tui/components/transcript/tool-execution.js';

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function makeRuntime(spy: { rendererCalls: number }) {
  return {
    slots: {
      resolveToolRenderer: () => (_input: unknown) => {
        spy.rendererCalls += 1;
        return new Text('x', 1, 0);
      },
      resolveToolPreview: () => undefined,
    },
    store: { currentSnapshot: { cwd: '/tmp' } },
  };
}

function card(status: string, args: Record<string, unknown> = { command: 'ls' }) {
  return { callId: 'c1', toolName: 'bash', args, status };
}

/** 卡片子树里找计时行（Text 文本含 "Running…"）。 */
function findElapsedLine(view: ToolCardView): unknown {
  return (view as { children: unknown[] }).children.find(
    (child) =>
      child instanceof Text &&
      typeof (child as { text?: unknown }).text === 'string' &&
      ((child as { text: string }).text.includes('Running…') ||
        (child as { text: string }).text.includes('Running…'.replace('…', '...'))),
  );
}

describe('ToolCardView 指纹 memo', () => {
  it('指纹不变的 update 不重建（渲染器零重调）', () => {
    const spy = { rendererCalls: 0 };
    const c = card('done');
    const view = new ToolCardView(
      makeRuntime(spy) as never,
      c as never,
      { expanded: false } as never,
      () => {},
    );
    assert.equal(spy.rendererCalls, 1);

    view.update(c as never); // 同一对象，字段引用全等
    view.update(c as never);
    assert.equal(spy.rendererCalls, 1, '指纹不变不应重调渲染器');

    c.args = { command: 'ls -la' }; // mapping 的字段级原位赋值形态
    view.update(c as never);
    assert.equal(spy.rendererCalls, 2, 'args 变更应重建');
  });

  it('status 变更触发重建（fingerprint 覆盖 status）', () => {
    const spy = { rendererCalls: 0 };
    const c = card('running');
    const view = new ToolCardView(
      makeRuntime(spy) as never,
      c as never,
      { expanded: false } as never,
      () => {},
    );
    assert.equal(spy.rendererCalls, 1);
    c.status = 'done'; // mapping 的字段级原位赋值形态
    view.update(c as never);
    assert.equal(spy.rendererCalls, 2);
    view.dispose();
  });
});

describe('ToolCardView 计时行（ElapsedLine）', () => {
  it('live 卡片挂计时行；完结后移除且不再重绘', async () => {
    const spy = { rendererCalls: 0 };
    let renders = 0;
    const view = new ToolCardView(
      makeRuntime(spy) as never,
      card('running') as never,
      { expanded: false } as never,
      () => {
        renders += 1;
      },
    );
    assert.ok(findElapsedLine(view), 'live 卡片应有 Running… 计时行');

    view.update(card('done') as never);
    assert.equal(findElapsedLine(view), undefined, 'done 后计时行应移除');

    const atDone = renders;
    await wait(600); // 覆盖 250ms tick 余量
    assert.equal(renders, atDone, '完结后计时行不应再触发重绘');
  });

  it('done 态构造不挂计时行', () => {
    const view = new ToolCardView(
      makeRuntime({ rendererCalls: 0 }) as never,
      card('done') as never,
      { expanded: false } as never,
      () => {},
    );
    assert.equal(findElapsedLine(view), undefined);
  });

  it('dispose 停表且幂等', async () => {
    let renders = 0;
    const view = new ToolCardView(
      makeRuntime({ rendererCalls: 0 }) as never,
      card('running') as never,
      { expanded: false } as never,
      () => {
        renders += 1;
      },
    );
    view.dispose();
    view.dispose(); // 幂等
    const atDispose = renders;
    await wait(600);
    assert.equal(renders, atDispose, 'dispose 后不应再有计时行重绘');
  });
});
