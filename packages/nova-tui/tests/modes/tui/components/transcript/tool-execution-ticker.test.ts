/**
 * ToolCardView 指纹 memo + 计时行（现算语义）测试：
 * - 指纹不变的 update 不重建（渲染器零重调——消"每事件重建所有卡片"放大器）；
 * - live 卡片挂计时行（宿主 chrome，静态文本）；
 * - 计时行渲染时现算、不自持定时器：指纹不变时数字冻结，内容事件
 *  （partial 新引用）触发 rebuild 时按当前时刻现算刷新。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { Text } from '@earendil-works/pi-tui';

import { ToolCardView } from '../../../../../src/modes/tui/components/transcript/tool-execution.js';

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
      (child as { text: string }).text.includes('Running…'),
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
  });
});

describe('ToolCardView 计时行（渲染时现算）', () => {
  it('live 卡片挂计时行；完结后移除', () => {
    const view = new ToolCardView(
      makeRuntime({ rendererCalls: 0 }) as never,
      card('running') as never,
      { expanded: false } as never,
      () => {},
    );
    assert.ok(findElapsedLine(view), 'live 卡片应有 Running… 计时行');
    view.update(card('done') as never);
    assert.equal(findElapsedLine(view), undefined, 'done 后计时行应移除');
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

  it('现算语义：指纹不变时数字冻结，内容事件触发 rebuild 时按当前时刻刷新', () => {
    const startedAt = Date.now() - 3000; // 已跑 3s
    const c = { ...card('running'), startedAt };
    const view = new ToolCardView(
      makeRuntime({ rendererCalls: 0 }) as never,
      c as never,
      { expanded: false } as never,
      () => {},
    );
    const initial = (findElapsedLine(view) as { text: string }).text;
    assert.match(initial, /^Running… 3s/, `计时应从 startedAt 现算，实际: ${initial}`);

    // 指纹不变的 update：rebuild 被 memo 短路，数字冻结（不自转）
    view.update(c as never);
    assert.equal((findElapsedLine(view) as { text: string }).text, initial, '指纹不变数字应冻结');

    // 内容事件（partial 新引用）→ rebuild → 按当前时刻现算
    const before = Date.now();
    (c as { partial?: unknown }).partial = { content: [] };
    view.update(c as never);
    const refreshed = (findElapsedLine(view) as { text: string }).text;
    const expected = `Running… ${(((before - startedAt) / 1000).toFixed(1).replace(/\.0$/, ''))}s`;
    assert.equal(refreshed, expected, `内容事件后应按当前时刻现算: ${refreshed} vs ${expected}`);
  });
});

describe('ToolCardView 运行中文本去重', () => {
  it('running 无渲染器无内容：计时行在时不再出现 running… 占位行（不双行）', () => {
    const runtimeNoRenderer = {
      slots: {
        resolveToolRenderer: () => undefined,
        resolveToolPreview: () => undefined,
      },
      store: { currentSnapshot: { cwd: '/tmp' } },
    };
    const view = new ToolCardView(
      runtimeNoRenderer as never,
      card('running') as never,
      { expanded: false } as never,
      () => {},
    );
    const texts = (view as unknown as { children: unknown[] }).children
      .filter((child) => child instanceof Text)
      .map((child) => (child as { text: string }).text);
    const runningLines = texts.filter((t) => t.toLowerCase().includes('running'));
    assert.ok(findElapsedLine(view), 'live 卡片应有计时行');
    assert.equal(
      runningLines.length,
      1,
      `应只有计时行一处 Running，实际: ${JSON.stringify(runningLines)}`,
    );
  });
});
