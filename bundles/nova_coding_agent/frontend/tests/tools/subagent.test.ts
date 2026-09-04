/**
 * subagent 渲染器测试（frontend/tui/tools/subagent.ts）：
 * exit_code=-1 运行中哨兵的呈现语义——运行中 ⏳ + (running...) 占位、
 * 不提前落 usage/Total；完成后 ✓ + usage。历史 bug：流式结果携带默认值
 * exit_code=0，在跑任务提前亮 ✓（"确认后沙漏变对勾"）；runner 已修正为
 * 构造即 -1、终态回填真实退出码，本文件钉死渲染侧语义。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import renderSubagent from '../../tui/tools/subagent.js';

const identity = (s: string) => s;
const env = {
  cwd: '/tmp',
  colors: new Proxy({}, { get: () => identity }) as Record<string, (s: string) => string>,
  expanded: false,
};

function renderLines(output: unknown, width = 100): string[] {
  assert.ok(
    typeof output === 'object' && output !== null && typeof (output as any).render === 'function',
    '渲染器应产出组件形态',
  );
  return (output as { render: (w: number) => string[] }).render(width);
}

function input(partialDetails: Record<string, unknown>, status: 'running' | 'done' = 'running') {
  return {
    toolName: 'subagent',
    args: { agent: 'worker', task: 't' },
    status,
    partial: { details: partialDetails },
    env,
  } as any;
}

describe('subagent 渲染器：运行中哨兵语义', () => {
  it('single 运行中：⏳（无重复 running 文本），不落 usage 行', () => {
    const text = renderLines(
      renderSubagent(
        input({ mode: 'single', results: [{ agent: 'worker', task: 't', exit_code: -1 }] }),
      ),
    ).join('\n');
    assert.ok(text.includes('⏳'), '运行中应显示 ⏳');
    assert.ok(!text.includes('(running...)'), '⏳ 已表达运行中——不再重复占位文本');
    assert.ok(!text.includes('(no output)'), '运行中不得显示 (no output)');
    assert.ok(!/turns?/.test(text), '运行中不落 usage 行');
  });

  it('single 完成：✓ + usage 行', () => {
    const text = renderLines(
      renderSubagent(
        input(
          {
            mode: 'single',
            results: [
              {
                agent: 'worker',
                task: 't',
                exit_code: 0,
                usage: { turns: 2, input_tokens: 100, output_tokens: 50 },
                messages: [
                  { role: 'assistant', content: [{ type: 'text', text: '做完了' }] },
                ],
              },
            ],
          },
          'done',
        ),
      ),
    ).join('\n');
    assert.ok(text.includes('✓'));
    assert.ok(text.includes('做完了'));
    assert.ok(text.includes('2 turns'));
  });

  it('parallel 混合态：计数器只把真实完成的计入 done，在跑任务 ⏳', () => {
    const text = renderLines(
      renderSubagent(
        input({
          mode: 'parallel',
          results: [
            { agent: 'worker', task: 'a', exit_code: 0, messages: [] },
            { agent: 'worker', task: 'b', exit_code: -1, messages: [] },
          ],
        }),
      ),
    ).join('\n');
    assert.ok(text.includes('1/2 done, 1 running'), `计数器应真实: ${text.split('\n')[0]}`);
  });

  it('chain 中途：已完成 ✓ + 在跑 ⏳，Total 随累计过程可见', () => {
    const text = renderLines(
      renderSubagent(
        input({
          mode: 'chain',
          results: [
            {
              agent: 'scout',
              task: 's1',
              exit_code: 0,
              usage: { turns: 1, input_tokens: 10, output_tokens: 5 },
              messages: [{ role: 'assistant', content: [{ type: 'text', text: '侦查完' }] }],
            },
            { agent: 'worker', task: 's2', exit_code: -1, messages: [] },
          ],
        }),
      ),
    ).join('\n');
    assert.ok(text.includes('Step 1'), '已完成步骤应在列');
    assert.ok(text.includes('Step 2'), '在跑步骤应在列');
    assert.ok(text.includes('⏳'), '在跑步骤应 ⏳');
    // 消耗行过程可见（随 on_update 累计滚动——不再等完结/展开）
    assert.ok(text.includes('Total:'), '累计消耗过程可见');
    assert.ok(text.includes('1/2 steps'), '分母应为声明总步数（非当前 results 数）');
  });

  it('chain 步骤间隙瞬态：results 暂全完成但步数未满时头部仍 ⏳、Total 累计可见', () => {
    // 第 1 步刚完成、第 2 步首个事件未到的间隙帧：头部完成判定保持 ⏳
    //（分母按声明总步数），消耗行随累计数据过程可见
    const text = renderLines(
      renderSubagent({
        toolName: 'subagent',
        args: { chain: [{ agent: 'scout', task: 's1' }, { agent: 'worker', task: 's2' }] },
        status: 'running',
        partial: {
          details: {
            mode: 'chain',
            results: [
              {
                agent: 'scout',
                task: 's1',
                exit_code: 0,
                usage: { turns: 1, input_tokens: 10, output_tokens: 5 },
                messages: [],
              },
            ],
          },
        },
        env,
      } as any),
    ).join('\n');
    assert.ok(text.includes('⏳'), '步数未满时头部应 ⏳');
    assert.ok(text.includes('Total:'), '累计消耗过程可见');
  });

  it('chain 全部完成：落 Total 行且头部 ✓', () => {
    const text = renderLines(
      renderSubagent({
        toolName: 'subagent',
        args: { chain: [{ agent: 'scout', task: 's1' }, { agent: 'worker', task: 's2' }] },
        status: 'done',
        partial: {
          details: {
            mode: 'chain',
            results: [
              { agent: 'scout', task: 's1', exit_code: 0, usage: { turns: 1 }, messages: [] },
              { agent: 'worker', task: 's2', exit_code: 0, usage: { turns: 1 }, messages: [] },
            ],
          },
        },
        env,
      } as any),
    ).join('\n');
    assert.ok(text.includes('Total:'), '全链完成应落 Total 行');
    assert.ok(!text.includes('⏳'), '完成后不再有 ⏳');
  });
});
