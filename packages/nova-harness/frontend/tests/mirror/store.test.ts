import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { NovaBus } from '../../src/bus.js';
import type { NovaEventEnvelope } from '../../src/protocol/nova-wire.gen.js';
import { MirrorStore } from '../../src/mirror/store.js';
import type { SessionSnapshot, StoreChange } from '../../src/mirror/types.js';

function ev(type: string, data: unknown): NovaEventEnvelope {
  return { type, data } as unknown as NovaEventEnvelope;
}

/** 最小合法快照（契约形状 SessionStateResult，测试只关心被直写的字段）。 */
function makeSnapshot(): SessionSnapshot {
  return {
    sessionId: 's-1',
    sessionFile: null,
    sessionName: 'demo',
    cwd: '/tmp',
    model: { provider: 'volcengine', id: 'm1' },
    thinkingLevel: 'medium',
    supportsThinking: true,
    availableThinkingLevels: ['off', 'medium'],
    activeTools: ['bash'],
    messageCount: 0,
    pendingMessageCount: 0,
    steeringMessages: [],
    followUpMessages: [],
    isStreaming: false,
    isCompacting: false,
    isRetrying: false,
    autoRetryEnabled: true,
    autoCompactionEnabled: true,
    steeringMode: 'one-at-a-time',
    followUpMode: 'one-at-a-time',
    projectTrusted: true,
  };
}

describe('MirrorStore', () => {
  it('sync 重建快照与历史条目，并发布 session:synced', () => {
    const bus = new NovaBus();
    const store = new MirrorStore(bus);
    let synced = 0;
    bus.onDerived('session:synced', () => {
      synced += 1;
    });
    store.sync(makeSnapshot(), [
      { id: 'e1', type: 'message', message: { role: 'user', content: 'hi' } },
      { id: 'e2', type: 'message', message: { role: 'assistant', content: 'yo' } },
    ]);
    assert.equal(store.entries.length, 2);
    assert.equal(store.currentSnapshot?.sessionName, 'demo');
    assert.equal(synced, 1);
  });

  it('四事件直写快照：model / thinking / session_info / queue', () => {
    const store = new MirrorStore();
    store.sync(makeSnapshot(), []);
    const areas: string[] = [];
    store.subscribe((change: StoreChange) => areas.push(change.area));

    store.apply(ev('model_changed', { model: { provider: 'openai', id: 'gpt-5' }, previousModel: null, source: 'user' }));
    assert.deepEqual(store.currentSnapshot?.model, { provider: 'openai', id: 'gpt-5' });

    store.apply(ev('thinking_level_changed', { level: 'high' }));
    assert.equal(store.currentSnapshot?.thinkingLevel, 'high');

    // null → "off"（与后端 getSessionState 同一语义）
    store.apply(ev('thinking_level_changed', { level: null }));
    assert.equal(store.currentSnapshot?.thinkingLevel, 'off');

    store.apply(ev('session_info_changed', { name: '新名字' }));
    assert.equal(store.currentSnapshot?.sessionName, '新名字');

    store.apply(ev('queue_update', { steering: ['s1'], followUp: ['f1', 'f2'] }));
    assert.deepEqual(store.currentSnapshot?.steeringMessages, ['s1']);
    assert.deepEqual(store.currentSnapshot?.followUpMessages, ['f1', 'f2']);

    assert.deepEqual(areas, ['snapshot', 'snapshot', 'snapshot', 'snapshot', 'queue']);
  });

  it('model_changed 载荷形状异常时不写快照（防御）', () => {
    const store = new MirrorStore();
    store.sync(makeSnapshot(), []);
    store.apply(ev('model_changed', { model: { bogus: true }, previousModel: null, source: '' }));
    assert.deepEqual(store.currentSnapshot?.model, { provider: 'volcengine', id: 'm1' });
  });

  it('agent_start/agent_end 发布 turn 派生事件', () => {
    const bus = new NovaBus();
    const store = new MirrorStore(bus);
    store.sync(makeSnapshot(), []);
    const turns: string[] = [];
    bus.onDerived('turn:started', () => turns.push('start'));
    bus.onDerived('turn:ended', () => turns.push('end'));
    bus.publish(ev('agent_start', {}));
    bus.publish(ev('agent_end', { messages: [] }));
    assert.deepEqual(turns, ['start', 'end']);
    assert.equal(store.status, 'idle');
  });

  it('status 迁移各 emit 一次 status；其余事件不 emit', () => {
    const store = new MirrorStore();
    store.sync(makeSnapshot(), []);
    const areas: string[] = [];
    store.subscribe((change: StoreChange) => areas.push(change.area));

    store.apply(ev('agent_start', {})); // idle → working
    store.apply(ev('agent_end', { messages: [] })); // working → idle
    store.apply(ev('agent_start', {}));
    store.apply(ev('auto_compaction_start', { reason: 'manual' })); // working → compacting
    store.apply(ev('auto_compaction_end', {})); // compacting → working
    assert.deepEqual(
      areas.filter((area) => area === 'status'),
      ['status', 'status', 'status', 'status', 'status'],
    );

    // 其余事件（transcript 增量/快照直写）不触发 status 通知
    areas.length = 0;
    store.apply(ev('message_start', { message: { role: 'user', content: 'hi' } }));
    store.apply(ev('model_changed', { model: { provider: 'openai', id: 'gpt-5' }, previousModel: null, source: 'user' }));
    store.apply(ev('queue_update', { steering: [], followUp: [] }));
    store.apply(ev('auto_retry_start', {})); // working → retrying（迁移，emit）
    store.apply(ev('auto_retry_end', {})); // retrying → working（迁移，emit）
    store.apply(ev('tool_execution_update', { toolCallId: 'nope', partialResult: {} })); // 无变更
    assert.deepEqual(
      areas.filter((area) => area === 'status'),
      ['status', 'status'],
    );
  });

  it('status 未实际变化不重复 emit（重复 agent_start 防渲染风暴）', () => {
    const store = new MirrorStore();
    store.sync(makeSnapshot(), []);
    store.apply(ev('agent_start', {})); // → working
    const areas: string[] = [];
    store.subscribe((change: StoreChange) => areas.push(change.area));
    store.apply(ev('agent_start', {})); // 已 working——status 没变
    store.apply(ev('turn_start', {})); // 同上
    assert.equal(areas.filter((area) => area === 'status').length, 0);
  });

  it('mirror 经 bus 特权通道接收事件（transcript 增量）', () => {
    const bus = new NovaBus();
    const store = new MirrorStore(bus);
    store.sync(makeSnapshot(), []);
    bus.publish(
      ev('message_start', { message: { role: 'user', content: [{ type: 'text', text: 'hello' }] } }),
    );
    assert.equal(store.entries.length, 1);
    assert.equal(store.entries[0]?.kind, 'user');
  });

  it('生命周期布尔直写：compacting / retrying 随事件迁移', () => {
    const store = new MirrorStore();
    store.sync(makeSnapshot(), []);
    assert.equal(store.currentSnapshot?.isCompacting, false);

    store.apply(ev('auto_compaction_start', {}));
    assert.equal(store.currentSnapshot?.isCompacting, true);
    store.apply(ev('auto_compaction_end', {}));
    assert.equal(store.currentSnapshot?.isCompacting, false);

    store.apply(ev('auto_retry_start', {}));
    assert.equal(store.currentSnapshot?.isRetrying, true);
    store.apply(ev('auto_retry_end', {}));
    assert.equal(store.currentSnapshot?.isRetrying, false);
  });

  it('updateSnapshot：未 sync 时静默丢弃', () => {
    const store = new MirrorStore();
    store.updateSnapshot({ activeTools: ['bash'] });
    assert.equal(store.currentSnapshot, null);
  });

  it('updateSnapshot：补丁合并并通知', () => {
    const store = new MirrorStore();
    store.sync(makeSnapshot(), []);
    const areas: string[] = [];
    store.subscribe((change) => areas.push(change.area));
    store.updateSnapshot({ activeTools: ['read', 'grep'], autoRetryEnabled: false });
    const snapshot = store.currentSnapshot;
    assert.deepEqual(snapshot?.activeTools, ['read', 'grep']);
    assert.equal(snapshot?.autoRetryEnabled, false);
    // 未提及的字段保持
    assert.equal(snapshot?.sessionName, 'demo');
    assert.deepEqual(areas, ['snapshot']);
  });
});
