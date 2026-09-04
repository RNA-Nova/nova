/**
 * WorkAreaView（工作区视图）与数据助手测试：
 * 行 1（活动/计时/输出量估算/阶段/计数器）+ 清单区三态逐项行的语义钉死。
 */

import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';

import chalk from 'chalk';

// 测试环境非 TTY 时 chalk 自动降级为无色——流光断言需要色码，强制开启
chalk.level = 3;

import type { MirrorStore, TranscriptEntry } from 'nova-tui';

import {
  WorkAreaView,
  applyShimmer,
  formatElapsed,
  formatTokenEstimate,
  hasStreamingThinking,
  latestTodos,
  liveOutputChars,
  runningSubagentCount,
  runningToolCount,
  runningToolEntry,
} from '../../../../../src/modes/tui/components/status/work-area.js';

/** 剥掉 ANSI 色码（流光会给文本插色码，内容断言前先剥）。 */
function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*m/g, '');
}

function toolCallEntry(toolName: string, status: string, details?: unknown): TranscriptEntry {
  return {
    kind: 'toolCall',
    id: `c-${toolName}-${status}`,
    card: {
      callId: '1',
      toolName,
      args: {},
      status: status as never,
      result: details !== undefined ? { details } : undefined,
    },
  } as TranscriptEntry;
}

function assistantEntry(text: string, streaming: boolean, thinking?: string): TranscriptEntry {
  return { kind: 'assistant', id: `a-${text.length}`, text, streaming, thinking } as TranscriptEntry;
}

describe('latestTodos', () => {
  it('取最后一张 todo 卡片的清单', () => {
    const entries = [
      toolCallEntry('todo', 'done', { todos: [{ content: '旧计划', status: 'completed' }] }),
      toolCallEntry('bash', 'done'),
      toolCallEntry('todo', 'done', {
        todos: [
          { content: '新计划一', status: 'completed' },
          { content: '新计划二', status: 'in_progress' },
        ],
      }),
    ];
    assert.deepEqual(latestTodos(entries), [
      { content: '新计划一', status: 'completed' },
      { content: '新计划二', status: 'in_progress' },
    ]);
  });

  it('无 todo 卡片 → undefined；partial 详情兜底', () => {
    assert.equal(latestTodos([toolCallEntry('bash', 'done')]), undefined);
    const partial = toolCallEntry('todo', 'running');
    partial.card.partial = { details: { todos: [{ content: 'x', status: 'pending' }] } };
    assert.deepEqual(latestTodos([partial]), [{ content: 'x', status: 'pending' }]);
  });
});

describe('liveOutputChars / thinking / 计数器', () => {
  it('最近 assistant 流式中取文本长度，完结取 0', () => {
    assert.equal(liveOutputChars([assistantEntry('hello', true)]), 5);
    assert.equal(liveOutputChars([assistantEntry('hello', false)]), 0);
    assert.equal(liveOutputChars([]), 0);
  });

  it('hasStreamingThinking：仅流式且有 thinking 内容时为真', () => {
    assert.equal(hasStreamingThinking([assistantEntry('x', true, '在想')]), true);
    assert.equal(hasStreamingThinking([assistantEntry('x', true)]), false);
    assert.equal(hasStreamingThinking([assistantEntry('x', false, '在想')]), false);
  });

  it('runningToolCount / runningSubagentCount', () => {
    const entries = [
      toolCallEntry('read', 'done'),
      toolCallEntry('bash', 'running'),
      toolCallEntry('subagent', 'running'),
    ];
    entries[2].card.partial = {
      details: { results: [{ exit_code: -1 }, { exit_code: 0 }, { exit_code: -1 }] },
    };
    assert.equal(runningToolCount(entries), 2);
    assert.equal(runningSubagentCount(entries), 2);
    // 无结果帧的子代理卡片按 1 计
    assert.equal(runningSubagentCount([toolCallEntry('subagent', 'running')]), 1);
  });

  it('runningToolEntry 取最近在跑工具，全完结为 undefined', () => {
    const entry = runningToolEntry([toolCallEntry('read', 'done'), toolCallEntry('bash', 'running')]);
    assert.equal(entry?.card.toolName, 'bash');
    assert.equal(runningToolEntry([toolCallEntry('read', 'done')]), undefined);
  });
});

describe('formatTokenEstimate / formatElapsed', () => {
  it('字符数/4 换算与单位', () => {
    assert.equal(formatTokenEstimate(400), '100');
    assert.equal(formatTokenEstimate(4000), '1.0k');
    assert.equal(formatTokenEstimate(40000), '10k');
    assert.equal(formatTokenEstimate(400000), '100k');
  });

  it('计时粒度', () => {
    assert.equal(formatElapsed(12_000), '12s');
    assert.equal(formatElapsed(65_000), '1m 5s');
  });
});

describe('applyShimmer（流光扫过）', () => {
  it('窗口段高亮、其余暗色；剥码后原文不变', () => {
    const out = applyShimmer('Working…', 2, 3);
    const plain = stripAnsi(out);
    assert.equal(plain, 'Working…', '剥码后必须是原文');
    assert.notEqual(out, plain, '有 ANSI 样式注入');
  });

  it('pos 越界自动截断（扫入/扫出边界不产生错位）', () => {
    assert.equal(stripAnsi(applyShimmer('abc', -5, 2)), 'abc');
    assert.equal(stripAnsi(applyShimmer('abc', 99, 2)), 'abc');
  });
});

describe('WorkAreaView 渲染', () => {
  const tui = { requestRender() {}, requestRenderFull() {} };
  const liveViews: WorkAreaView[] = [];

  afterEach(() => {
    // WorkAreaView 的 500ms 定时器与 Loader 动画均未 unref——必须统一 dispose
    for (const view of liveViews.splice(0)) view.dispose();
  });

  function makeStore(entries: TranscriptEntry[]): MirrorStore {
    return { entries } as MirrorStore;
  }

  function makeView(entries: TranscriptEntry[]): WorkAreaView {
    const view = new WorkAreaView(tui as never, makeStore(entries), {}, () => {});
    liveViews.push(view);
    return view;
  }

  it('行 1 括号系（计时 · ↓ ~估算）+ 清单区三态逐项', () => {
    const view = makeView([
      assistantEntry('正在产出中', true),
      toolCallEntry('todo', 'done', {
        todos: [
          { content: '第一步', status: 'completed' },
          { content: '第二步', status: 'in_progress' },
          { content: '第三步', status: 'pending' },
        ],
      }),
    ]);
    const text = stripAnsi(view.render(100).join("\n"));
    assert.ok(text.includes('Working… ('), '行 1 为活动 + 括号系');
    assert.ok(text.includes('↓ ~'), '含估算输出量');
    assert.ok(text.includes('✓'), '完成项 ✓');
    assert.ok(text.includes('■ 第二步'), '在跑项 ■ 高亮');
    assert.ok(text.includes('□ 第三步'), '待办项 □');
    assert.ok(text.includes('└'), '首行 └ 连接符');
  });

  it('有工具在跑时活动为 Running <tool>…；thinking 标记出现；无 todo 不出清单行', () => {
    const view = makeView([assistantEntry('x', true, '思考中'), toolCallEntry('bash', 'running')]);
    const text = stripAnsi(view.render(100).join("\n"));
    assert.ok(text.includes('Running bash… ('));
    assert.ok(text.includes('thinking'));
    assert.ok(!text.includes('✓') && !text.includes('□'), '无 todo 不出清单行');
  });

  it('清单超 6 项：封顶 + 溢出合并行', () => {
    const todos = Array.from({ length: 9 }, (_, i) => ({
      content: `第${i + 1}项`,
      status: i === 0 ? 'in_progress' : 'pending',
    }));
    const view = makeView([toolCallEntry('todo', 'done', { todos })]);
    const text = stripAnsi(view.render(100).join("\n"));
    assert.ok(text.includes('… 还有 3 项'), '溢出合并行');
    assert.ok(!text.includes('第7项'), '超顶项不渲染');
  });
});
