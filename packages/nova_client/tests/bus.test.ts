import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { NovaEventEnvelope } from '../src/protocol/nova-wire.gen.js';
import { NovaBus } from '../src/bus.js';

function ev(type: string, data: unknown = {}): NovaEventEnvelope {
  return { type, data } as unknown as NovaEventEnvelope;
}

describe('NovaBus', () => {
  it('mirror 特权订阅先于一切观察者', () => {
    const bus = new NovaBus();
    const order: string[] = [];
    bus.on('agent_start', () => order.push('observer'));
    bus.subscribeMirror(() => order.push('mirror'));
    bus.publish(ev('agent_start'));
    assert.deepEqual(order, ['mirror', 'observer']);
  });

  it('观察者异常被隔离，不影响其他观察者与 mirror', () => {
    const bus = new NovaBus();
    const seen: string[] = [];
    bus.subscribeMirror(() => seen.push('mirror'));
    bus.on('agent_start', () => {
      throw new Error('boom');
    });
    bus.on('agent_start', () => seen.push('second'));
    // console.error 会被打一次——静默掉避免污染测试输出
    const orig = console.error;
    console.error = () => {};
    try {
      bus.publish(ev('agent_start'));
    } finally {
      console.error = orig;
    }
    assert.deepEqual(seen, ['mirror', 'second']);
  });

  it('async 观察者的 rejection 同样被隔离（不成 unhandledRejection）', async () => {
    const bus = new NovaBus();
    const seen: string[] = [];
    bus.on('agent_start', async () => {
      throw new Error('async boom');
    });
    bus.on('agent_start', () => seen.push('sync-ok'));
    bus.onDerived('turn:started', async () => {
      throw new Error('async derived boom');
    });
    bus.onDerived('turn:started', () => seen.push('derived-ok'));
    const orig = console.error;
    console.error = () => {};
    try {
      bus.publish(ev('agent_start'));
      bus.publishDerived('turn:started', undefined);
      // 让 rejection 有机会传播——若无 .catch 兜住，测试进程会因 unhandledRejection 挂掉
      await new Promise((resolve) => setImmediate(resolve));
    } finally {
      console.error = orig;
    }
    assert.deepEqual(seen, ['sync-ok', 'derived-ok']);
  });

  it('mirror 异常不吞（状态正确性响亮失败）', () => {
    const bus = new NovaBus();
    bus.subscribeMirror(() => {
      throw new Error('mirror boom');
    });
    assert.throws(() => bus.publish(ev('agent_start')), /mirror boom/);
  });

  it('通配符观察者收到所有类型', () => {
    const bus = new NovaBus();
    const types: string[] = [];
    bus.on('*', (event) => types.push(event.type));
    bus.publish(ev('agent_start'));
    bus.publish(ev('turn_end', {}));
    assert.deepEqual(types, ['agent_start', 'turn_end']);
  });

  it('派生事件按名分发、异常隔离', () => {
    const bus = new NovaBus();
    const seen: string[] = [];
    bus.onDerived('turn:started', () => {
      throw new Error('boom');
    });
    bus.onDerived('turn:started', () => seen.push('a'));
    bus.onDerived('turn:ended', () => seen.push('b'));
    const orig = console.error;
    console.error = () => {};
    try {
      bus.publishDerived('turn:started', undefined);
      bus.publishDerived('turn:ended', undefined);
    } finally {
      console.error = orig;
    }
    assert.deepEqual(seen, ['a', 'b']);
  });

  it('退订生效', () => {
    const bus = new NovaBus();
    let count = 0;
    const off = bus.on('agent_start', () => {
      count += 1;
    });
    bus.publish(ev('agent_start'));
    off();
    bus.publish(ev('agent_start'));
    assert.equal(count, 1);
  });
});
