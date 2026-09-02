/**
 * 扩展 UI 原语测试：customLocal 模态宿主语义（done 解决/幂等/异常降级）、
 * CompositeAutocompleteProvider（合并/去重/来源路由）、autocomplete slot 注册。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { Container, Text } from '@earendil-works/pi-tui';

import { DialogController } from '../../../../src/modes/tui/controllers/dialogs.js';

/** 最小可用的 DialogController 假环境（本地框路径不触 RPC）。 */
function makeDialogs() {
  const children: unknown[] = [];
  const fakeTui = { setFocus: () => {}, requestRender: () => {} };
  const fakeContainer = {
    clear: () => children.splice(0, children.length),
    addChild: (c: unknown) => children.push(c),
  };
  const editorRef = { current: {} };
  const runtime = {
    onUIRequest: () => {},
    onUICancel: () => {},
    onUINotice: () => {},
  };
  const dialogs = new DialogController(
    fakeTui as never,
    fakeContainer as never,
    editorRef as never,
    {} as never,
    runtime as never,
    { cwd: '/tmp' } as never,
  );
  return { dialogs, children };
}

describe('customLocal（模态对话框宿主）', () => {
  it('done(result) 解决 Promise 并恢复编辑器槽位', async () => {
    const { dialogs, children } = makeDialogs();
    let capturedDone: ((r?: string) => void) | undefined;
    const promise = dialogs.customLocal<string>((_env, done) => {
      capturedDone = done;
      return new Text('hi', 0, 0);
    });
    assert.equal(dialogs.isActive, true); // 本地框开着
    capturedDone!('结果');
    assert.equal(await promise, '结果');
    assert.equal(dialogs.isActive, false); // 已恢复
    assert.ok(children.length > 0); // 编辑器回槽
  });

  it('done 幂等（多次调用只解决一次）；无参 done = 取消语义', async () => {
    const { dialogs } = makeDialogs();
    let capturedDone: ((r?: string) => void) | undefined;
    const promise = dialogs.customLocal<string>((_env, done) => {
      capturedDone = done;
      return new Text('x', 0, 0);
    });
    capturedDone!(undefined);
    capturedDone!('迟到'); // 第二次无效
    assert.equal(await promise, undefined);
  });

  it('工厂抛错/产物非组件 → undefined 解决（不挂起扩展）', async () => {
    const { dialogs } = makeDialogs();
    assert.equal(
      await dialogs.customLocal(() => {
        throw new Error('boom');
      }),
      undefined,
    );
    assert.equal(await dialogs.customLocal(() => 42), undefined);
    assert.equal(dialogs.isActive, false);
  });

  it('overlay 选项：经 tui.showOverlay 挂载，done 时 hide', async () => {
    const { dialogs } = makeDialogs();
    let shown: unknown;
    let hidden = false;
    // 覆盖 tui 的 showOverlay（fake 里补这个方法）
    (dialogs as unknown as { tui: unknown }).tui = {
      setFocus: () => {},
      requestRender: () => {},
      showOverlay: (component: unknown) => {
        shown = component;
        return { hide: () => (hidden = true) };
      },
    };
    let capturedDone: (() => void) | undefined;
    const promise = dialogs.customLocal((_env, done) => {
      capturedDone = done as () => void;
      return new Text('overlay', 0, 0);
    }, { overlay: { anchor: 'center' } });
    assert.ok(shown !== undefined); // 走了 showOverlay
    capturedDone!();
    await promise;
    assert.equal(hidden, true);
  });
});

describe('registerAutocompleteProvider（autocomplete slot）', () => {
  it('thunk 包装注册，resolve 后调用得原对象', async () => {
    const { SlotRegistry } = await import('nova-client');
    const { createExtensionUIAPI } = await import('nova-client');
    const slots = new SlotRegistry();
    const api = createExtensionUIAPI({ slots, source: 'test-pkg' });
    const provider = { getSuggestions: async () => null, applyCompletion: () => ({}) };
    api.registerAutocompleteProvider?.('mine', provider);
    const entries = slots.list().filter(({ key }) => key === 'autocomplete:mine');
    assert.equal(entries.length, 1);
    const producer = slots.resolve('autocomplete:mine' as never) as () => unknown;
    assert.equal(producer(), provider); // thunk 解开即原对象
  });
});


describe('dialog:* 自定义词汇路由', () => {
  /** 带 slots/sendUIResponse 的假 runtime（dialog: 路由的最小面）。 */
  function makeDialogsWithSlots(factory?: unknown) {
    const children: unknown[] = [];
    const responses: Array<{ id: string; payload: unknown }> = [];
    const fakeTui = { setFocus: () => {}, requestRender: () => {} };
    const fakeContainer = {
      clear: () => children.splice(0, children.length),
      addChild: (c: unknown) => children.push(c),
    };
    const runtime = {
      onUIRequest: () => {},
      onUICancel: () => {},
      onUINotice: () => {},
      slots: { resolve: (key: string) => (key === 'dialog:q' ? factory : undefined) },
      sendUIResponse: (id: string, payload: unknown) => responses.push({ id, payload }),
    };
    const dialogs = new DialogController(
      fakeTui as never,
      fakeContainer as never,
      { current: {} } as never,
      {} as never,
      runtime as never,
      { cwd: '/tmp' } as never,
    );
    return { dialogs, responses, children };
  }

  it('已注册 dialog：工厂经 (env, params, done) 调起，done(value) 按 {value} 应答', () => {
    let captured: { params?: unknown; done?: (r?: unknown) => void };
    const { dialogs, responses } = makeDialogsWithSlots(
      (env: unknown, params: unknown, done: (r?: unknown) => void) => {
        captured = { params, done };
        return new Text('q', 0, 0);
      },
    );
    (dialogs as never as { handle: (r: unknown) => void }).handle({
      id: 'r1',
      component: 'dialog:q',
      params: { question: '选哪个？' },
    });
    assert.deepEqual(captured!.params, { question: '选哪个？' });
    captured!.done!({ answer: 'A' });
    assert.deepEqual(responses, [{ id: 'r1', payload: { value: { answer: 'A' } } }]);
  });

  it('done(undefined) 按 cancelled 应答；未注册 dialog 也按 cancelled（不挂起后端）', () => {
    let capturedDone: ((r?: unknown) => void) | undefined;
    const { dialogs, responses } = makeDialogsWithSlots((_e: unknown, _p: unknown, done: (r?: unknown) => void) => {
      capturedDone = done;
      return new Text('q', 0, 0);
    });
    (dialogs as never as { handle: (r: unknown) => void }).handle({ id: 'r2', component: 'dialog:q', params: {} });
    capturedDone!(undefined);
    assert.deepEqual(responses, [{ id: 'r2', payload: { cancelled: true } }]);

    const none = makeDialogsWithSlots(undefined);
    (none.dialogs as never as { handle: (r: unknown) => void }).handle({ id: 'r3', component: 'dialog:ghost', params: {} });
    assert.deepEqual(none.responses, [{ id: 'r3', payload: { cancelled: true } }]);
  });

  it('工厂抛错/产物非组件：按 cancelled 降级（不炸控制器）', () => {
    const { dialogs, responses } = makeDialogsWithSlots(() => {
      throw new Error('boom');
    });
    (dialogs as never as { handle: (r: unknown) => void }).handle({ id: 'r4', component: 'dialog:q', params: {} });
    assert.deepEqual(responses, [{ id: 'r4', payload: { cancelled: true } }]);

    const bad = makeDialogsWithSlots(() => 42);
    (bad.dialogs as never as { handle: (r: unknown) => void }).handle({ id: 'r5', component: 'dialog:q', params: {} });
    assert.deepEqual(bad.responses, [{ id: 'r5', payload: { cancelled: true } }]);
  });
});


describe('set_status 命名通知路由', () => {
  function makeDialogsWithStatus(onExtensionStatus?: (key: string, text: string | undefined) => void) {
    const fakeTui = { setFocus: () => {}, requestRender: () => {} };
    const fakeContainer = { clear: () => {}, addChild: () => {} };
    const runtime = { onUIRequest: () => {}, onUICancel: () => {}, onUINotice: () => {} };
    return new DialogController(
      fakeTui as never,
      fakeContainer as never,
      { current: {} } as never,
      {} as never,
      runtime as never,
      { cwd: '/tmp' } as never,
      onExtensionStatus,
    );
  }

  it('set_status 通知 → footer 回调（key 幂等覆盖）', () => {
    const calls: Array<[string, string | undefined]> = [];
    const dialogs = makeDialogsWithStatus((key, text) => calls.push([key, text]));
    (dialogs as never as { showNotice: (n: unknown) => void }).showNotice({
      name: 'set_status',
      params: { key: 'plan-mode', text: '⏸ plan' },
    });
    (dialogs as never as { showNotice: (n: unknown) => void }).showNotice({
      name: 'set_status',
      params: { key: 'plan-mode', text: '📋 1/2' },
    });
    assert.deepEqual(calls, [
      ['plan-mode', '⏸ plan'],
      ['plan-mode', '📋 1/2'],
    ]);
  });

  it('空文本 → undefined（清除该状态位）；无名 key 忽略', () => {
    const calls: Array<[string, string | undefined]> = [];
    const dialogs = makeDialogsWithStatus((key, text) => calls.push([key, text]));
    (dialogs as never as { showNotice: (n: unknown) => void }).showNotice({
      name: 'set_status',
      params: { key: 'plan-mode', text: '' },
    });
    (dialogs as never as { showNotice: (n: unknown) => void }).showNotice({
      name: 'set_status',
      params: { text: 'no-key' },
    });
    assert.deepEqual(calls, [['plan-mode', undefined]]);
  });

  it('未注入出口时静默降级（不炸、不影响其余通知）', () => {
    const dialogs = makeDialogsWithStatus(undefined);
    (dialogs as never as { showNotice: (n: unknown) => void }).showNotice({
      name: 'set_status',
      params: { key: 'k', text: 'v' },
    }); // 不抛即通过
  });

  it('空 progress 通知 → clearNotice（登录结束清除 Waiting 提示）；空非 progress 丢弃', () => {
    const calls: string[] = [];
    const fakeTui = { setFocus: () => {}, requestRender: () => {} };
    const fakeContainer = { clear: () => {}, addChild: () => {} };
    const runtime = { onUIRequest: () => {}, onUICancel: () => {}, onUINotice: () => {} };
    const status = {
      showNotice: () => void calls.push('show'),
      clearNotice: () => void calls.push('clear'),
    };
    const dialogs = new DialogController(
      fakeTui as never,
      fakeContainer as never,
      { current: {} } as never,
      status as never,
      runtime as never,
      { cwd: '/tmp' } as never,
      undefined,
    );
    const fire = (params: unknown) =>
      (dialogs as never as { showNotice: (n: unknown) => void }).showNotice({
        name: 'notify',
        params,
      });
    fire({ message: '', type: 'progress' }); // → clear
    fire({ message: '', type: 'info' }); // → 丢弃
    fire({ message: '已完成', type: 'info' }); // → show
    assert.deepEqual(calls, ['clear', 'show']);
  });
});
