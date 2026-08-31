/**
 * commands/directory 三源合并 + HelpViewer 分组渲染测试。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { SlotRegistry, commandSlot, type NovaUIRuntime } from 'nova-client';

import {
  buildCommandDirectory,
  LOCAL_COMMANDS,
} from '../../../../src/modes/tui/commands/directory.js';
import { HelpViewer } from '../../../../src/modes/tui/components/dialogs/help-viewer.js';

function makeRuntime(rpcCommands: Array<{ name: string; description?: string; source?: string }>) {
  const runtime = {
    invoke: async () => ({ commands: rpcCommands }),
    slots: new SlotRegistry(),
  } as unknown as NovaUIRuntime;
  return runtime;
}

describe('buildCommandDirectory', () => {
  it('prompt/skill 来源标注为模板/技能类型（与真命令区分）', async () => {
    const runtime = makeRuntime([
      { name: 'refactor', description: '重构', source: 'prompt' },
      { name: 'skill:commit', description: '提交', source: 'skill' },
      { name: 'compact', description: '压缩', source: 'extension' },
    ]);
    const entries = await buildCommandDirectory(runtime);
    const byName = new Map(entries.map((e) => [e.name, e]));
    assert.equal(byName.get('refactor')?.kind, 'prompt');
    assert.equal(byName.get('skill:commit')?.kind, 'skill');
    assert.equal(byName.get('compact')?.kind, 'command');
  });

  it('三源合并：后端 + 本地 + slot（按名去重）', async () => {
    const runtime = makeRuntime([
      { name: 'compact', description: '压缩' },
      { name: 'tree', description: '后端版' },
    ]);
    const fn = (args: string) => void args;
    (fn as { description?: string }).description = 'slot 版';
    runtime.slots.register(commandSlot('tree'), fn, 'pkg');
    runtime.slots.register(commandSlot('packages'), (args: string) => void args, 'builtin');

    const entries = await buildCommandDirectory(runtime);
    const byName = new Map(entries.map((e) => [e.name, e]));

    assert.equal(byName.get('compact')?.source, 'backend');
    assert.equal(byName.get('tree')?.source, 'slot'); // slot 覆盖后端（分发现实）
    assert.equal(byName.get('tree')?.description, 'slot 版');
    assert.equal(byName.get('packages')?.source, 'slot');
    assert.equal(byName.get('theme')?.source, 'local');
    // 无重复
    const names = entries.map((e) => e.name);
    assert.equal(new Set(names).size, names.length);
  });

  it('本地 /help 在目录中（描述指向完整目录）', async () => {
    const runtime = makeRuntime([{ name: 'help', description: '后端版 help' }]);
    const entries = await buildCommandDirectory(runtime);
    const help = entries.find((e) => e.name === 'help');
    assert.equal(help?.source, 'local'); // 本地遮蔽后端（分发同序）
    assert.match(help?.description ?? '', /含本地命令/);
  });

  it('enabled 过滤三源同判生效', async () => {
    const runtime = makeRuntime([
      { name: 'compact', description: 'x' },
      { name: 'secret', description: 'y' },
    ]);
    const entries = await buildCommandDirectory(runtime, (name) => name !== 'secret');
    assert.ok(!entries.some((e) => e.name === 'secret'));
    assert.ok(entries.some((e) => e.name === 'compact'));
  });

  it('null/空名后端条目被消毒丢弃', async () => {
    const runtime = makeRuntime([
      { name: null as unknown as string, description: 'bad' },
      { name: '', description: 'bad2' },
      { name: 'ok' },
    ]);
    const entries = await buildCommandDirectory(runtime);
    assert.ok(entries.every((e) => typeof e.name === 'string' && e.name.length > 0));
  });
});

describe('HelpViewer', () => {
  it('按分组渲染（后端→模板→本地→扩展），名称列对齐', () => {
    const viewer = new HelpViewer(
      [
        { name: 'compact', description: '压缩', source: 'backend', kind: 'command' },
        { name: 'refactor', description: '重构', source: 'backend', kind: 'prompt' },
        { name: 'theme', description: '主题', source: 'local', kind: 'command' },
        { name: 'packages', description: '包', source: 'slot', kind: 'command' },
      ],
      () => {},
    );
    const out = viewer.render(80).join('\n');
    const backendIdx = out.indexOf('后端命令');
    const promptIdx = out.indexOf('提示词模板');
    const localIdx = out.indexOf('本地命令');
    const slotIdx = out.indexOf('扩展命令');
    assert.ok(backendIdx !== -1 && promptIdx !== -1 && localIdx !== -1 && slotIdx !== -1);
    assert.ok(backendIdx < promptIdx && promptIdx < localIdx && localIdx < slotIdx);
    assert.ok(out.includes('/compact'));
    assert.ok(out.includes('/refactor'));
    assert.ok(out.includes('/theme'));
    assert.ok(out.includes('/packages'));
  });

  it('空组不渲染标题；esc/q/ctrl+c 关闭', () => {
    let closed = false;
    const viewer = new HelpViewer(
      [{ name: 'theme', source: 'local', kind: 'command' }],
      () => {
        closed = true;
      },
    );
    const out = viewer.render(80).join('\n');
    assert.ok(!out.includes('后端命令'));
    assert.ok(!out.includes('扩展命令'));
    assert.ok(!out.includes('提示词模板'));
    viewer.handleInput('q');
    assert.equal(closed, true);
  });

  it('本地命令表与 autocomplete 同源（LOCAL_COMMANDS 导出）', () => {
    const names = LOCAL_COMMANDS.map((c) => c.name);
    for (const expected of ['help', 'theme', 'settings', 'copy', 'hotkeys', 'debug', 'share', 'changelog', 'quit']) {
      assert.ok(names.includes(expected), `缺少本地命令 ${expected}`);
    }
  });
});
