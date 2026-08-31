/**
 * ResourcesView 测试：资源区拉取与两态渲染（compact 计数 / expanded 分组清单）。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { NovaUIRuntime } from 'nova-client';

import { ResourcesView } from '../../../../../src/modes/tui/components/layout/resources.js';
import type { ExpansionState } from '../../../../../src/modes/tui/components/transcript/expansion.js';

function makeRuntime(responses: Record<string, unknown>): NovaUIRuntime {
  return {
    invoke: async (method: string) => {
      if (method in responses) {
        const value = responses[method];
        if (value instanceof Error) throw value;
        return value;
      }
      return {};
    },
  } as unknown as NovaUIRuntime;
}

const FULL_RESPONSES = {
  listSkills: { skills: [{ name: 'path-helper' }, { name: 'rna-fold' }] },
  listPromptTemplates: { prompts: [{ name: 'review' }] },
  getCommands: { commands: [{ name: 'model' }, { name: 'tree' }, { name: 'fork' }] },
  pkgList: {
    // 线上真实形态：身份键控映射（非 {packages: []} 数组包装）
    'local:/path/to/nova_coding_agent': { name: 'nova-coding-agent', scope: 'user' },
  },
};

describe('ResourcesView', () => {
  it('compact：单行计数', async () => {
    const expansion: ExpansionState = { expanded: false };
    const view = new ResourcesView(makeRuntime(FULL_RESPONSES), expansion);
    await view.refresh();
    const out = view.render(100).join('\n');
    assert.match(out, /2 skills/);
    assert.match(out, /1 prompts/);
    assert.match(out, /3 commands/);
    assert.match(out, /1 packages/);
    assert.doesNotMatch(out, /path-helper/); // 名称不在 compact 态出现
  });

  it('expanded：分组清单（名称逐行）', async () => {
    const expansion: ExpansionState = { expanded: true };
    const view = new ResourcesView(makeRuntime(FULL_RESPONSES), expansion);
    await view.refresh();
    const out = view.render(100).join('\n');
    assert.match(out, /Skills \(2\)/);
    assert.match(out, /path-helper/);
    assert.match(out, /rna-fold/);
    assert.match(out, /Commands \(3\)/);
  });

  it('展开态切换：rebuild 不重拉数据', async () => {
    const expansion: ExpansionState = { expanded: false };
    let calls = 0;
    const runtime = {
      invoke: async () => {
        calls++;
        return { skills: [{ name: 's1' }] };
      },
    } as unknown as NovaUIRuntime;
    const view = new ResourcesView(runtime, expansion);
    await view.refresh();
    const callsAfterRefresh = calls;
    expansion.expanded = true;
    view.rebuild(); // 纯重建——零 RPC
    assert.equal(calls, callsAfterRefresh);
    assert.match(view.render(100).join('\n'), /s1/);
  });

  it('RPC 全失败：空计数不炸（启动降级）', async () => {
    const expansion: ExpansionState = { expanded: false };
    const view = new ResourcesView(
      makeRuntime({ listSkills: new Error('offline') }),
      expansion,
    );
    await view.refresh();
    const out = view.render(100).join('\n');
    assert.match(out, /0 skills/);
  });
});
