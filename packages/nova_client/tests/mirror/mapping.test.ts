/**
 * mirror apply 层测试（终态契约）：item 三帧应用 + 视图派生 + 域通知推导。
 *
 * 归约语义（流式切分/工具配对/中断收尾/包消息映射）全部归服务器
 * ``server/reduction``——本文件只测前端 apply 层自己的职责：
 * 追加/合并/替换、幂等与防御、item → 视图条目派生、域通知的工作状态推导。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { NovaEventEnvelope, NovaWireItem } from '../../src/protocol/nova-wire.gen.js';
import {
  applyItemDelta,
  applyRuntimeEvent,
  createTranscriptState,
  deriveEntries,
  itemToEntry,
} from '../../src/mirror/mapping.js';

/** 测试用事件构造：字面量转契约类型（单测聚焦 apply 语义，不逐字段凑全型）。 */
function ev(type: string, data: unknown): NovaEventEnvelope {
  return { type, data } as unknown as NovaEventEnvelope;
}

function item(partial: Record<string, unknown>): NovaWireItem {
  return {
    id: 'i1',
    status: null,
    source: 'agent',
    ts: 1,
    ...partial,
  } as unknown as NovaWireItem;
}

describe('item 三帧应用', () => {
  it('item_started 追加 + 视图派生（userMessage → user 条目）', () => {
    const state = createTranscriptState();
    const changed = applyRuntimeEvent(
      state,
      ev('item_started', {
        item: item({ id: 'u1', type: 'userMessage', source: 'user', content: [{ type: 'text', text: 'hello' }] }),
      }),
    );
    assert.equal(changed, true);
    assert.equal(state.items.length, 1);
    const entries = deriveEntries(state);
    assert.equal(entries[0]?.kind, 'user');
    assert.equal((entries[0] as { text: string }).text, 'hello');
  });

  it('item_started 同 id 幂等替换（重放/重复帧防御）', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(state, ev('item_started', { item: item({ id: 't1', type: 'toolCall', tool: 'a' }) }));
    applyRuntimeEvent(state, ev('item_started', { item: item({ id: 't1', type: 'toolCall', tool: 'b' }) }));
    assert.equal(state.items.length, 1);
    assert.equal((state.items[0] as { tool: string }).tool, 'b');
  });

  it('item_delta：text/output 白名单追加，其余替换', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('item_started', { item: item({ id: 'm1', type: 'agentMessage', status: 'running', text: 'Hel' }) }),
    );
    applyRuntimeEvent(state, ev('item_delta', { id: 'm1', delta: { text: 'lo' } }));
    assert.equal((state.items[0] as { text: string }).text, 'Hello');
    // 非白名单字段替换（status 枚举字符串不拼接）
    applyRuntimeEvent(state, ev('item_delta', { id: 'm1', delta: { status: 'done' } }));
    assert.equal(state.items[0].status, 'done');
  });

  it('item_delta 未见 started 的 id 忽略；item_completed 未见 started 尾插（防御）', () => {
    const state = createTranscriptState();
    assert.equal(applyRuntimeEvent(state, ev('item_delta', { id: 'x', delta: { text: 'a' } })), false);
    applyRuntimeEvent(state, ev('item_completed', { item: item({ id: 'x', type: 'agentMessage', text: '终', status: 'done' }) }));
    assert.equal(state.items.length, 1);
    assert.equal(state.items[0].status, 'done');
  });

  it('流式→定稿全链：started(running) → delta → completed(done)', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(state, ev('item_started', { item: item({ id: 'a1', type: 'agentMessage', status: 'running', text: '' }) }));
    applyRuntimeEvent(state, ev('item_delta', { id: 'a1', delta: { text: '半句' } }));
    applyRuntimeEvent(state, ev('item_delta', { id: 'a1', delta: { text: '说完' } }));
    const live = itemToEntry(state.items[0], state);
    assert.equal(live?.kind === 'assistant' && live.streaming, true);
    applyRuntimeEvent(
      state,
      ev('item_completed', { item: item({ id: 'a1', type: 'agentMessage', status: 'done', text: '半句话说完了' }) }),
    );
    const final = deriveEntries(state)[0];
    assert.equal(final?.kind === 'assistant' && final.text, '半句话说完了');
    assert.equal(final?.kind === 'assistant' && final.streaming, false);
  });
});

describe('视图派生', () => {
  it('thinking / toolCall / 包级变体 / compaction / branchSummary 的条目映射', () => {
    const state = createTranscriptState();
    const items = [
      item({ id: 'th', type: 'thinking', status: 'done', text: '想' }),
      item({ id: 'tc', type: 'toolCall', status: 'done', tool: 'read', args: { p: 1 }, ts: 2 }),
      item({ id: 'pk', type: 'bashExecution', status: 'done', command: 'ls', output: 'o', ts: 3 }),
      item({ id: 'cp', type: 'compaction', status: 'done', summary: 's', ts: 4 }),
      item({ id: 'br', type: 'branchSummary', status: 'done', summary: 'b', fromId: 'e9', ts: 5 }),
    ];
    for (const it of items) applyRuntimeEvent(state, ev('item_started', { item: it }));
    const entries = deriveEntries(state);
    assert.deepEqual(entries.map((e) => e.kind), ['assistant', 'toolCall', 'custom', 'custom', 'custom']);
    assert.equal(entries[0]?.kind === 'assistant' && entries[0].thinking, '想');
    assert.equal(entries[2]?.kind === 'custom' && entries[2].customType, 'bashExecution');
    assert.equal(entries[3]?.kind === 'custom' && entries[3].customType, 'compaction');
    assert.equal(entries[4]?.kind === 'custom' && entries[4].customType, 'branch_summary');
  });

  it('assistant 中止/失败的呈现映射（cancelled→中止文案，failed→错误行）', () => {
    const state = createTranscriptState();
    const cancelled = itemToEntry(item({ id: 'a', type: 'agentMessage', status: 'cancelled', text: '半' }), state);
    assert.equal(cancelled?.kind === 'assistant' && cancelled.stopReason, 'aborted');
    assert.equal(cancelled?.kind === 'assistant' && cancelled.errorMessage, '已中止');
    const failed = itemToEntry(
      item({ id: 'b', type: 'agentMessage', status: 'failed', text: '', error: 'context overflow' }),
      state,
    );
    assert.equal(failed?.kind === 'assistant' && failed.stopReason, 'error');
    assert.equal(failed?.kind === 'assistant' && failed.errorMessage, 'context overflow');
  });

  it('本地通知与会话内容按 ts 归并排显', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(state, ev('item_started', { item: item({ id: 'u1', type: 'userMessage', ts: 100, content: [] }) }));
    state.notices.push({ id: 'n1', level: 'info', text: '提醒', ts: 50 });
    state.notices.push({ id: 'n2', level: 'error', text: '错误', ts: 200 });
    const entries = deriveEntries(state);
    assert.deepEqual(entries.map((e) => e.id), ['n1', 'u1', 'n2']);
  });
});

describe('域通知推导', () => {
  it('agent_start/end 驱动 status', () => {
    const state = createTranscriptState();
    assert.equal(state.status, 'idle');
    applyRuntimeEvent(state, ev('agent_start', {}));
    assert.equal(state.status, 'working');
    applyRuntimeEvent(state, ev('agent_end', {}));
    assert.equal(state.status, 'idle');
  });

  it('compaction / retry 事件推导 spinner 状态', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(state, ev('agent_start', {}));
    applyRuntimeEvent(state, ev('auto_compaction_start', { reason: 'threshold' }));
    assert.equal(state.status, 'compacting');
    assert.equal(state.compactionReason, 'threshold');
    applyRuntimeEvent(state, ev('auto_compaction_end', { willRetry: false }));
    assert.equal(state.status, 'idle');
    assert.equal(state.compactionReason, null);

    applyRuntimeEvent(state, ev('auto_retry_start', { attempt: 2, maxAttempts: 3, delayMs: 1500 }));
    assert.equal(state.status, 'retrying');
    assert.deepEqual(state.retryStatus, { attempt: 2, maxAttempts: 3, delayMs: 1500 });
    assert.equal(state.lastRetryAttempt, 2);
    applyRuntimeEvent(state, ev('auto_retry_end', {}));
    assert.equal(state.status, 'working');
    assert.equal(state.retryStatus, null);
  });

  it('extension_error 产 error 通知（可见性纪律）', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(state, ev('extension_error', { event: 'load', error: 'boom' }));
    const entries = deriveEntries(state);
    assert.equal(entries[0]?.kind, 'notice');
    assert.match((entries[0] as { text: string }).text, /扩展错误（load）：boom/);
  });

  it('cache_miss：显著阈值才产通知；低于阈值忽略', () => {
    const state = createTranscriptState();
    assert.equal(applyRuntimeEvent(state, ev('cache_miss', { missedTokens: 1000, missedCost: 0 })), false);
    applyRuntimeEvent(state, ev('cache_miss', { missedTokens: 25_000, missedCost: 0.2, modelChanged: true }));
    const entries = deriveEntries(state);
    assert.equal(entries.length, 1);
    assert.match((entries[0] as { text: string }).text, /缓存 miss（模型切换后）：25\.0k tokens 被重新计费（约 \$0\.20）/);
  });

  it('未知事件类型静默忽略（向前兼容）', () => {
    const state = createTranscriptState();
    assert.equal(applyRuntimeEvent(state, ev('future_event', { x: 1 })), false);
    assert.equal(state.items.length, 0);
  });
});

describe('applyItemDelta 直测', () => {
  it('白名单双端字符串追加；其余一律替换；未知键透传', () => {
    const target = item({ id: 't', type: 'bashExecution', output: 'a' });
    applyItemDelta(target, { output: 'b' });
    assert.equal((target as { output: string }).output, 'ab');
    applyItemDelta(target, { exitCode: 1, output: 0 });
    assert.equal((target as { exitCode: number }).exitCode, 1);
    assert.equal((target as { output: unknown }).output, 0); // 非字符串不追加
    applyItemDelta(target, { novelField: 'x' });
    assert.equal((target as { novelField: string }).novelField, 'x');
  });
});
