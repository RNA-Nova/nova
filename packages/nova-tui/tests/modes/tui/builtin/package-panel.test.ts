/**
 * 内建包面板测试（dogfood 验收）：registerCommand 注册 + 列表/详情/更新/卸载流程。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  SlotRegistry,
  commandSlot,
  createExtensionUIAPI,
  type ExtensionUIContext,
} from 'nova-tui';

import { registerPackagePanel } from '../../../../src/modes/tui/builtin/package-panel.js';

/** ctx 桩：invoke 按方法名返回脚本，select 按脚本出值。 */
function makeCtx(options: {
  packages?: Record<string, unknown>;
  updates?: Array<{ displayName: string }>;
  selectScript?: Array<string | undefined>;
}) {
  const calls = { invoke: [] as string[], notify: [] as string[], refresh: 0 };
  const ctx: ExtensionUIContext = {
    invoke: async (method: string) => {
      calls.invoke.push(method);
      if (method === 'pkgList') return options.packages ?? {};
      if (method === 'pkgCheckUpdates') return { updates: options.updates ?? [] };
      return {};
    },
    notify: (message) => void calls.notify.push(message),
    select: async (_title, items) => {
      void items;
      return options.selectScript?.shift();
    },
    refreshPackages: async () => void calls.refresh++,
  };
  return { ctx, calls };
}

function makePanel(ctx: ExtensionUIContext) {
  const slots = new SlotRegistry();
  const api = createExtensionUIAPI({
    slots,
    source: 'builtin',
    uiContext: ctx,
  });
  registerPackagePanel(api);
  const handler = slots.resolve<string, unknown>(commandSlot('packages')) as (
    args: string,
  ) => Promise<void>;
  return { slots, handler };
}

const TWO_PACKAGES = {
  'nova-coding-agent': {
    name: 'nova-coding-agent',
    version: '1.0.0',
    description: '官方编程包',
    scope: 'user',
    tools: [{}, {}],
    skills: [],
    agents: [{}],
  },
  'rna-pack': { name: 'rna-pack', version: '0.1.0', scope: 'project', tools: [{}] },
};

describe('包面板（内建扩展 dogfood）', () => {
  it('registerCommand 注册进 command:packages（builtin 来源）', () => {
    const { ctx } = makeCtx({});
    const { slots } = makePanel(ctx);
    assert.equal(slots.sourceOf(commandSlot('packages')), 'builtin');
  });

  it('空包列表 → 提示；无 select 通道 → 降级提示', async () => {
    const { ctx, calls } = makeCtx({ packages: {} });
    await makePanel(ctx).handler('');
    assert.match(calls.notify[0] ?? '', /没有已安装的包/);

    const bare: ExtensionUIContext = {
      invoke: async () => ({}),
      notify: (m) => void calls.notify.push(m),
    };
    await makePanel(bare).handler('');
    assert.match(calls.notify[1] ?? '', /选择器通道/);
  });

  it('列表 → 选中包 → 更新动作（pkgUpdate + refreshPackages）', async () => {
    const { ctx, calls } = makeCtx({
      packages: TWO_PACKAGES,
      updates: [{ displayName: 'rna-pack' }], // 角标
      selectScript: ['nova-coding-agent', 'update'],
    });
    await makePanel(ctx).handler('');
    assert.ok(calls.invoke.includes('pkgList'));
    assert.ok(calls.invoke.includes('pkgUpdate'));
    assert.equal(calls.refresh, 1);
    assert.match(calls.notify[0] ?? '', /已更新 nova-coding-agent/);
  });

  it('卸载动作（pkgUninstall + refresh + 通知）', async () => {
    const { ctx, calls } = makeCtx({
      packages: TWO_PACKAGES,
      selectScript: ['rna-pack', 'uninstall'],
    });
    await makePanel(ctx).handler('');
    assert.ok(calls.invoke.includes('pkgUninstall'));
    assert.equal(calls.refresh, 1);
    assert.match(calls.notify[0] ?? '', /已卸载 rna-pack/);
  });

  it('作用域切换项递归重开（local 翻转）', async () => {
    const selectScript: Array<string | undefined> = ['__toggle_scope__', undefined];
    const { ctx, calls } = makeCtx({ packages: TWO_PACKAGES, selectScript });
    const localFlags: boolean[] = [];
    const originalInvoke = ctx.invoke;
    ctx.invoke = async (method, params) => {
      if (method === 'pkgList') localFlags.push((params as { local: boolean }).local);
      return originalInvoke(method, params);
    };
    await makePanel(ctx).handler('');
    assert.deepEqual(localFlags, [false, true]); // 用户级 → 项目级翻转
  });
});
