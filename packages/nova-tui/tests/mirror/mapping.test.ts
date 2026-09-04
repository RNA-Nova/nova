import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { NovaEventEnvelope } from '../../src/protocol/nova-wire.gen.js';
import {
  applyRuntimeEvent,
  createTranscriptState,
} from '../../src/mirror/mapping.js';

/** 测试用事件构造：字面量转契约类型（单测聚焦映射语义，不逐字段凑全型）。 */
function ev(type: string, data: unknown): NovaEventEnvelope {
  return { type, data } as unknown as NovaEventEnvelope;
}

describe('applyRuntimeEvent', () => {
  it('agent_start/end 驱动 status', () => {
    const state = createTranscriptState();
    assert.equal(state.status, 'idle');
    applyRuntimeEvent(state, ev('agent_start', {}));
    assert.equal(state.status, 'working');
    applyRuntimeEvent(state, ev('agent_end', {}));
    assert.equal(state.status, 'idle');
  });

  it('user 消息进 transcript', () => {
    const state = createTranscriptState();
    const changed = applyRuntimeEvent(
      state,
      ev('message_start', { message: { role: 'user', content: [{ type: 'text', text: 'hello' }] } }),
    );
    assert.equal(changed, true);
    assert.equal(state.entries.length, 1);
    assert.deepEqual(state.entries[0]?.kind, 'user');
    assert.equal((state.entries[0] as { text: string }).text, 'hello');
  });

  it('assistant 流式生命周期：start → update（累积替换）→ end', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_start', { message: { role: 'assistant', id: 'a1', content: [] } }),
    );
    applyRuntimeEvent(
      state,
      ev('message_update', { message: { role: 'assistant', content: [{ type: 'text', text: '半句' }] } }),
    );
    applyRuntimeEvent(
      state,
      ev('message_update', {
        message: { role: 'assistant', content: [{ type: 'text', text: '半句话说完' }] },
      }),
    );
    const entry = state.entries[0];
    assert.equal(entry?.kind, 'assistant');
    if (entry?.kind === 'assistant') {
      assert.equal(entry.text, '半句话说完');
      assert.equal(entry.streaming, true);
    }
    applyRuntimeEvent(
      state,
      ev('message_end', { message: { role: 'assistant', id: 'a1', content: [] } }),
    );
    if (entry?.kind === 'assistant') assert.equal(entry.streaming, false);
  });

  it('流式中途插入外来事件：notice/custom/其他消息 end 均不打断流式槽', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_start', { message: { role: 'assistant', id: 'a1', content: [] } }),
    );
    // 旁路条目插入（cache_miss 显著阈值以上才进条目）
    applyRuntimeEvent(state, ev('cache_miss', { missedTokens: 30000 }));
    // 外来 message_end：user 角色（steer 注入类）——不关 assistant 流式槽
    applyRuntimeEvent(
      state,
      ev('message_end', { message: { role: 'user', id: 'u9', content: [] } }),
    );
    // 外来 assistant message_end：id 不匹配——同样不关槽
    applyRuntimeEvent(
      state,
      ev('message_end', { message: { role: 'assistant', id: 'a2', content: [] } }),
    );
    // 流式更新照常落到 a1 条目
    applyRuntimeEvent(
      state,
      ev('message_update', {
        message: { role: 'assistant', id: 'a1', content: [{ type: 'text', text: '仍在流' }] },
      }),
    );
    const entry = state.entries.find((e) => e.kind === 'assistant');
    if (entry?.kind === 'assistant') {
      assert.equal(entry.text, '仍在流');
      assert.equal(entry.streaming, true); // 槽未被误关
    }
    // 匹配的 message_end 才关槽
    applyRuntimeEvent(
      state,
      ev('message_end', { message: { role: 'assistant', id: 'a1', content: [] } }),
    );
    if (entry?.kind === 'assistant') assert.equal(entry.streaming, false);
  });

  it('thinking 与正文分离：assistant 条目的 thinking 独立成字段', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_start', {
        message: {
          role: 'assistant',
          id: 'a1',
          content: [
            { type: 'thinking', thinking: '先想想' },
            { type: 'text', text: '答案' },
          ],
        },
      }),
    );
    applyRuntimeEvent(
      state,
      ev('message_update', {
        message: {
          role: 'assistant',
          content: [
            { type: 'thinking', thinking: '先想想' },
            { type: 'thinking', thinking: '再想想' },
            { type: 'text', text: '答案写完了' },
          ],
        },
      }),
    );
    const entry = state.entries[0];
    if (entry?.kind !== 'assistant') throw new Error('expected assistant entry');
    assert.equal(entry.text, '答案写完了');
    assert.equal(entry.thinking, '先想想\n\n再想想');
  });

  it('工具调用卡片：start → update(partial) → end(result)', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('tool_execution_start', { toolCallId: 'tc1', toolName: 'bash', args: { command: 'ls' } }),
    );
    applyRuntimeEvent(
      state,
      ev('tool_execution_update', {
        toolCallId: 'tc1',
        partialResult: { content: [{ type: 'text', text: 'out...' }] },
      }),
    );
    applyRuntimeEvent(
      state,
      ev('tool_execution_end', {
        toolCallId: 'tc1',
        result: { content: [{ type: 'text', text: 'done' }], details: { exit_code: 0 } },
        isError: false,
      }),
    );
    const entry = state.entries[0];
    assert.equal(entry?.kind, 'toolCall');
    if (entry?.kind === 'toolCall') {
      assert.equal(entry.card.toolName, 'bash');
      assert.equal(entry.card.status, 'done');
      assert.equal(entry.card.partial?.content?.[0]?.text, 'out...');
      assert.deepEqual(entry.card.result?.details, { exit_code: 0 });
    }
  });

  it('工具执行失败标记 error', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('tool_execution_start', { toolCallId: 'tc1', toolName: 'bash', args: {} }),
    );
    applyRuntimeEvent(
      state,
      ev('tool_execution_end', { toolCallId: 'tc1', result: {}, isError: true }),
    );
    const entry = state.entries[0];
    if (entry?.kind === 'toolCall') assert.equal(entry.card.status, 'error');
  });

  it('compaction / retry 事件推导 spinner 状态', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(state, ev('auto_compaction_start', {}));
    assert.equal(state.status, 'compacting');
    // will_retry=true（overflow 错误恢复）→ 继续 working
    applyRuntimeEvent(state, ev('auto_compaction_end', { willRetry: true }));
    assert.equal(state.status, 'working');
    // 手动/threshold（will_retry=false 或缺省）→ run 结束回 idle
    applyRuntimeEvent(state, ev('auto_compaction_start', {}));
    assert.equal(state.status, 'compacting');
    applyRuntimeEvent(state, ev('compaction_end', { willRetry: false }));
    assert.equal(state.status, 'idle');
    applyRuntimeEvent(state, ev('auto_retry_start', {}));
    assert.equal(state.status, 'retrying');
  });

  it('联合之外的事件类型不进 transcript（向前兼容，静默忽略）', () => {
    const state = createTranscriptState();
    const changed = applyRuntimeEvent(
      state,
      ev('model_select', { provider: 'x', modelId: 'y' }),
    );
    assert.equal(changed, false);
    assert.equal(state.entries.length, 0);
  });

  it('R7：message_end(aborted) 把未完结工具卡片收尾为 error', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('tool_execution_start', { toolCallId: 'tc1', toolName: 'bash', args: {} }),
    );
    applyRuntimeEvent(
      state,
      ev('tool_execution_start', { toolCallId: 'tc2', toolName: 'read', args: {} }),
    );
    // tc2 正常完结，tc1 仍挂着
    applyRuntimeEvent(
      state,
      ev('tool_execution_end', { toolCallId: 'tc2', result: {}, isError: false }),
    );
    const changed = applyRuntimeEvent(
      state,
      ev('message_end', { message: { role: 'assistant', stopReason: 'aborted', content: [] } }),
    );
    assert.equal(changed, true);
    const aborted = state.entries[0];
    if (aborted?.kind === 'toolCall') {
      assert.equal(aborted.card.status, 'error');
      assert.match(String(aborted.card.result?.content?.[0]?.text), /已中止/);
    }
    const done = state.entries[1];
    if (done?.kind === 'toolCall') assert.equal(done.card.status, 'done');
    assert.equal(state.openToolCalls.size, 0);
  });

  it('R7：message_end(error) 用 error_message 收尾未完结卡片', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('tool_execution_start', { toolCallId: 'tc1', toolName: 'bash', args: {} }),
    );
    applyRuntimeEvent(
      state,
      ev('message_end', {
        message: { role: 'assistant', stopReason: 'error', errorMessage: 'API 500', content: [] },
      }),
    );
    const entry = state.entries[0];
    if (entry?.kind === 'toolCall') {
      assert.equal(entry.card.status, 'error');
      assert.match(String(entry.card.result?.content?.[0]?.text), /API 500/);
    }
  });

  it('R7：agent_end 兜底收尾遗留的运行中卡片', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('tool_execution_start', { toolCallId: 'tc1', toolName: 'bash', args: {} }),
    );
    applyRuntimeEvent(state, ev('agent_end', { messages: [] }));
    const entry = state.entries[0];
    if (entry?.kind === 'toolCall') assert.equal(entry.card.status, 'error');
    assert.equal(state.status, 'idle');
    assert.equal(state.openToolCalls.size, 0);
  });

  it('R7：正常 message_end（stop）不动运行中的卡片', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('tool_execution_start', { toolCallId: 'tc1', toolName: 'bash', args: {} }),
    );
    applyRuntimeEvent(
      state,
      ev('message_end', { message: { role: 'assistant', stopReason: 'toolUse', content: [] } }),
    );
    const entry = state.entries[0];
    if (entry?.kind === 'toolCall') assert.equal(entry.card.status, 'running');
    assert.equal(state.openToolCalls.size, 1);
  });

  it('两阶段：message_update 的 toolCall 块建 streaming 卡并逐段累积参数', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_start', { message: { role: 'assistant', id: 'a1', content: [] } }),
    );
    // 第一段：参数部分到达（json-repair 增量解析后的部分对象）
    applyRuntimeEvent(
      state,
      ev('message_update', {
        message: {
          role: 'assistant',
          content: [
            {
              type: 'toolCall',
              id: 'tc1',
              name: 'edit',
              arguments: { path: 'src/a.ts' },
            },
          ],
        },
      }),
    );
    let card = state.entries.find((e) => e.kind === 'toolCall');
    assert.ok(card && card.kind === 'toolCall');
    assert.equal(card.card.status, 'streaming');
    assert.equal(card.card.argsComplete, false);
    assert.deepEqual(card.card.args, { path: 'src/a.ts' });

    // 第二段：参数继续累积（同 id 更新，不重复建卡）
    applyRuntimeEvent(
      state,
      ev('message_update', {
        message: {
          role: 'assistant',
          content: [
            {
              type: 'toolCall',
              id: 'tc1',
              name: 'edit',
              arguments: { path: 'src/a.ts', edits: [{ oldText: 'x' }] },
            },
          ],
        },
      }),
    );
    assert.equal(state.entries.filter((e) => e.kind === 'toolCall').length, 1);
    card = state.entries.find((e) => e.kind === 'toolCall');
    if (card?.kind === 'toolCall') {
      assert.deepEqual(card.card.args, { path: 'src/a.ts', edits: [{ oldText: 'x' }] });
      assert.equal(card.card.status, 'streaming');
    }
  });

  it('两阶段：message_end 正常结束标记 argsComplete（执行前预览时点）', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_start', { message: { role: 'assistant', id: 'a1', content: [] } }),
    );
    applyRuntimeEvent(
      state,
      ev('message_update', {
        message: {
          role: 'assistant',
          content: [
            { type: 'toolCall', id: 'tc1', name: 'edit', arguments: { path: 'a' } },
          ],
        },
      }),
    );
    applyRuntimeEvent(
      state,
      ev('message_end', {
        message: { role: 'assistant', stopReason: 'toolUse', content: [] },
      }),
    );
    const card = state.entries.find((e) => e.kind === 'toolCall');
    if (card?.kind === 'toolCall') {
      assert.equal(card.card.status, 'streaming'); // 参数完整但执行未开始
      assert.equal(card.card.argsComplete, true);
    }
  });

  it('两阶段：tool_execution_start 复用流式卡片（streaming → running，args 不动）', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_start', { message: { role: 'assistant', id: 'a1', content: [] } }),
    );
    applyRuntimeEvent(
      state,
      ev('message_update', {
        message: {
          role: 'assistant',
          content: [
            { type: 'toolCall', id: 'tc1', name: 'bash', arguments: { command: 'ls' } },
          ],
        },
      }),
    );
    applyRuntimeEvent(
      state,
      ev('tool_execution_start', {
        toolCallId: 'tc1',
        toolName: 'bash',
        args: { command: 'ls' },
      }),
    );
    assert.equal(state.entries.filter((e) => e.kind === 'toolCall').length, 1);
    const card = state.entries.find((e) => e.kind === 'toolCall');
    if (card?.kind === 'toolCall') {
      assert.equal(card.card.status, 'running');
      assert.deepEqual(card.card.args, { command: 'ls' });
    }
  });

  it('两阶段：tool_execution_start 无流式前置直接建 running 卡（防御路径）', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('tool_execution_start', { toolCallId: 'tc9', toolName: 'read', args: { path: 'x' } }),
    );
    const card = state.entries[0];
    if (card?.kind === 'toolCall') {
      assert.equal(card.card.status, 'running');
      assert.equal(card.card.argsComplete, true);
    }
  });

  it('两阶段：abort 时流式中的卡片同样被 R7 收尾', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_start', { message: { role: 'assistant', id: 'a1', content: [] } }),
    );
    applyRuntimeEvent(
      state,
      ev('message_update', {
        message: {
          role: 'assistant',
          content: [
            { type: 'toolCall', id: 'tc1', name: 'edit', arguments: { path: 'a' } },
          ],
        },
      }),
    );
    applyRuntimeEvent(
      state,
      ev('message_end', { message: { role: 'assistant', stopReason: 'aborted', content: [] } }),
    );
    const card = state.entries.find((e) => e.kind === 'toolCall');
    if (card?.kind === 'toolCall') {
      assert.equal(card.card.status, 'error');
      assert.match(String(card.card.result?.content?.[0]?.text), /已中止/);
    }
    assert.equal(state.openToolCalls.size, 0);
  });

  it('entry_appended：custom 条目实时进 transcript，非 custom 忽略', () => {
    const state = createTranscriptState();
    const changed = applyRuntimeEvent(
      state,
      ev('entry_appended', {
        entry: { id: 'e1', type: 'custom', customType: 'git_status', data: { branch: 'main' } },
      }),
    );
    assert.equal(changed, true);
    const entry = state.entries[0];
    if (entry?.kind === 'custom') {
      assert.equal(entry.id, 'e1');
      assert.equal(entry.customType, 'git_status');
      assert.deepEqual(entry.data, { branch: 'main' });
    }
    // 非 custom 条目不进（消息/压缩各有专属通道）
    const skipped = applyRuntimeEvent(
      state,
      ev('entry_appended', { entry: { id: 'e2', type: 'message' } }),
    );
    assert.equal(skipped, false);
    assert.equal(state.entries.length, 1);
  });

  it('retry 文案：重试后 abort → 卡片与错误行带重试次数', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(state, ev('agent_start', {}));
    applyRuntimeEvent(state, ev('auto_retry_start', { attempt: 2, maxAttempts: 3 }));
    applyRuntimeEvent(
      state,
      ev('tool_execution_start', { toolCallId: 'tc1', toolName: 'bash', args: {} }),
    );
    applyRuntimeEvent(
      state,
      ev('message_start', { message: { role: 'assistant', id: 'a1', content: [] } }),
    );
    applyRuntimeEvent(
      state,
      ev('message_end', { message: { role: 'assistant', stopReason: 'aborted', content: [] } }),
    );
    const card = state.entries.find((e) => e.kind === 'toolCall');
    if (card?.kind === 'toolCall') {
      assert.match(String(card.card.result?.content?.[0]?.text), /第 2 次重试后中止/);
    }
    const assistant = state.entries.find((e) => e.kind === 'assistant');
    if (assistant?.kind === 'assistant') {
      assert.equal(assistant.errorMessage, '第 2 次重试后中止');
    }
    assert.equal(state.lastRetryAttempt, 0);
  });

  it('cache_miss：显著 miss 产 notice 条目（文案含 tokens 与成本）', () => {
    const state = createTranscriptState();
    const changed = applyRuntimeEvent(
      state,
      ev('cache_miss', {
        missedTokens: 45000,
        missedCost: 0.45,
        idleMs: 1000,
        modelChanged: false,
      }),
    );
    assert.equal(changed, true);
    const entry = state.entries[0];
    if (entry?.kind === 'notice') {
      assert.match(entry.text, /缓存 miss：45\.0k tokens 被重新计费（约 \$0\.45）/);
    }
  });

  it('cache_miss：低于显著阈值不产生条目；模型切换/超 TTL 文案变体', () => {
    const state = createTranscriptState();
    // 低于阈值（2 万 tokens 且 <$0.1）→ 不打扰
    const changed = applyRuntimeEvent(
      state,
      ev('cache_miss', { missedTokens: 5000, missedCost: 0.01, idleMs: 0, modelChanged: false }),
    );
    assert.equal(changed, false);
    assert.equal(state.entries.length, 0);

    // 模型切换标签
    applyRuntimeEvent(
      state,
      ev('cache_miss', { missedTokens: 30000, missedCost: 0, idleMs: 0, modelChanged: true }),
    );
    const entry = state.entries[0];
    if (entry?.kind === 'notice') assert.match(entry.text, /模型切换后/);

    // 空闲超 TTL 标签
    applyRuntimeEvent(
      state,
      ev('cache_miss', {
        missedTokens: 30000,
        missedCost: 0,
        idleMs: 7 * 60 * 1000,
        modelChanged: false,
      }),
    );
    const entry2 = state.entries[1];
    if (entry2?.kind === 'notice') assert.match(entry2.text, /空闲 7 分钟后/);
  });

  it('状态详情：retry/compaction 详情随事件记录与清空', () => {
    const state = createTranscriptState();
    // retry 详情（指示器倒计时数据源）
    applyRuntimeEvent(
      state,
      ev('auto_retry_start', { attempt: 2, maxAttempts: 3, delayMs: 5000 }),
    );
    assert.equal(state.status, 'retrying');
    assert.deepEqual(state.retryStatus, { attempt: 2, maxAttempts: 3, delayMs: 5000 });
    applyRuntimeEvent(state, ev('auto_retry_end', { success: true }));
    assert.equal(state.retryStatus, null);
    assert.equal(state.status, 'working');

    // compaction reason（manual + overflow 变体）
    applyRuntimeEvent(state, ev('compaction_start', { reason: 'manual' }));
    assert.equal(state.compactionReason, 'manual');
    applyRuntimeEvent(state, ev('compaction_end', {}));
    assert.equal(state.compactionReason, null);

    applyRuntimeEvent(state, ev('auto_compaction_start', { reason: 'overflow' }));
    assert.equal(state.status, 'compacting');
    assert.equal(state.compactionReason, 'overflow');
    applyRuntimeEvent(state, ev('auto_compaction_end', {}));
    assert.equal(state.compactionReason, null);
  });
});


describe('custom 消息映射（扩展注入 / 用户工具）', () => {
  it('role=custom 的扩展消息进 transcript（customType 判别）', () => {
    const state = createTranscriptState();
    const changed = applyRuntimeEvent(
      state,
      ev('message_end', {
        message: { role: 'custom', customType: 'info', content: 'hello info', display: true },
      }),
    );
    assert.equal(changed, true);
    assert.equal(state.entries.length, 1);
    const entry = state.entries[0] as { kind: string; customType: string; data: unknown };
    assert.equal(entry.kind, 'custom');
    assert.equal(entry.customType, 'info');
  });

  it('display=false 为上下文注入：不进转录', () => {
    const state = createTranscriptState();
    const changed = applyRuntimeEvent(
      state,
      ev('message_end', {
        message: { role: 'custom', customType: 'ctx', content: 'x', display: false },
      }),
    );
    assert.equal(changed, false);
    assert.equal(state.entries.length, 0);
  });

  it('bash 用户工具：流式 chunk 聚合为单卡片，终态消息定稿不重复', () => {
    const state = createTranscriptState();
    // start 事件先行（后端执行前发射）：命令串即刻进卡片数据
    applyRuntimeEvent(
      state,
      ev('user_tool', { tool: 'bash', event: 'start', data: { command: 'echo hi', excludeFromContext: false }, callId: 'c1' }),
    );
    assert.equal(state.entries.length, 1);
    const seeded = state.entries[0] as { customType: string; data: { command: string; output: string } };
    assert.equal(seeded.customType, 'bashExecution');
    assert.equal(seeded.data.command, 'echo hi'); // `$ command` 头即时渲染
    assert.equal(seeded.data.output, '');

    applyRuntimeEvent(
      state,
      ev('user_tool', { tool: 'bash', event: 'output', data: { chunk: 'hello ' }, callId: 'c1' }),
    );
    applyRuntimeEvent(
      state,
      ev('user_tool', { tool: 'bash', event: 'output', data: { chunk: 'world' }, callId: 'c1' }),
    );
    assert.equal(state.entries.length, 1); // 聚合单卡片
    const streaming = state.entries[0] as { customType: string; data: { command: string; output: string } };
    assert.equal(streaming.customType, 'bashExecution');
    assert.equal(streaming.data.output, 'hello world');
    assert.equal(streaming.data.command, 'echo hi'); // chunk 合并不丢命令串

    applyRuntimeEvent(
      state,
      ev('message_end', {
        message: { role: 'bashExecution', command: 'echo hi', output: 'hello world', exitCode: 0 },
      }),
    );
    assert.equal(state.entries.length, 1); // 定稿合并不新建
    const finalized = state.entries[0] as {
      customType: string;
      data: { command: string; exitCode: number };
    };
    assert.equal(finalized.data.command, 'echo hi');
    assert.equal(finalized.data.exitCode, 0);
  });

  it('无流式的 bashExecution（拦截替换执行等）：直接建卡', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_end', {
        message: { role: 'bashExecution', command: 'vim', output: '', exitCode: 0 },
      }),
    );
    assert.equal(state.entries.length, 1);
    assert.equal((state.entries[0] as { customType: string }).customType, 'bashExecution');
  });

  it('toolResult 不被 custom 分支吞掉（走原有忽略路径）', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_end', { message: { role: 'toolResult', content: 'out' } }),
    );
    assert.equal(state.entries.length, 0);
  });
});

describe('数据面：计时锚点与 thinking 摘要', () => {
  it('工具卡片创建即带 startedAt（计时锚点数据化——重建不归零）', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_start', { message: { role: 'assistant', id: 'a1', content: [] } }),
    );
    applyRuntimeEvent(
      state,
      ev('message_update', {
        message: {
          role: 'assistant',
          id: 'a1',
          content: [{ type: 'toolCall', id: 'c1', name: 'bash', arguments: { command: 'ls' } }],
        },
      }),
    );
    const card = state.entries.find((e) => e.kind === 'toolCall');
    assert.ok(card?.kind === 'toolCall' && typeof card.card.startedAt === 'number');
    assert.ok((card as { card: { startedAt: number } }).card.startedAt <= Date.now());
  });

  it('message_end 写入 thinkingDurationMs 与按类聚合的 toolCounts', () => {
    const state = createTranscriptState();
    applyRuntimeEvent(
      state,
      ev('message_start', { message: { role: 'assistant', id: 'a1', timestamp: 1000, content: [] } }),
    );
    applyRuntimeEvent(
      state,
      ev('message_end', {
        message: {
          role: 'assistant',
          id: 'a1',
          timestamp: 4200,
          content: [
            { type: 'text', text: 'done' },
            { type: 'toolCall', id: 'c1', name: 'bash', arguments: {} },
            { type: 'toolCall', id: 'c2', name: 'read', arguments: {} },
            { type: 'toolCall', id: 'c3', name: 'read', arguments: {} },
          ],
        },
      }),
    );
    const entry = state.entries[0];
    assert.ok(entry?.kind === 'assistant');
    if (entry?.kind === 'assistant') {
      assert.equal(entry.thinkingDurationMs, 3200);
      assert.deepEqual(entry.toolCounts, { bash: 1, read: 2 });
    }
  });
});
