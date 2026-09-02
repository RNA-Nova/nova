/**
 * RegionHost 测试：两态分流（声明式块 / 逃生舱组件）、替换重判别、异常静默。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { NovaUIRuntime, regionSlot } from 'nova-client';
import { Text } from '@earendil-works/pi-tui';

import { RegionHost, type RegionEnv } from '../../../../../src/modes/tui/components/layout/region-host.js';
import { registerBuiltinBlocks } from '../../../../../src/modes/tui/blocks/index.js';

function makeHost(region = 'widget') {
  const runtime = new NovaUIRuntime({ slotsBootstrap: registerBuiltinBlocks });
  const env = { cwd: '/tmp' } as RegionEnv; // 测试不触达 tui/colors
  return { runtime, host: new RegionHost(runtime, region, env) };
}

describe('RegionHost', () => {
  it('无注册 → 空态（零行）', () => {
    const { host } = makeHost();
    assert.deepEqual(host.render(80), []);
  });

  it('声明式 producer：块经适配渲染；指纹缓存复用组件', () => {
    const { runtime, host } = makeHost();
    let value = '部件A';
    runtime.slots.register(
      regionSlot('widget'),
      () => [{ kind: 'markdown', text: value }],
      'pkg',
    );
    assert.match(host.render(80).join('\n'), /部件A/);
    const first = (host as unknown as { blockComponents: unknown }).blockComponents;
    host.render(80); // 输出不变 → 复用
    assert.equal(
      (host as unknown as { blockComponents: unknown }).blockComponents,
      first,
    );
    value = '部件B'; // 变化 → 重建
    assert.match(host.render(80).join('\n'), /部件B/);
  });

  it('逃生舱组件工厂：建厂一次直挂（重复渲染不重建）', () => {
    const { runtime, host } = makeHost();
    let builds = 0;
    const component = new Text('活部件', 1, 0);
    runtime.slots.register(
      regionSlot('widget'),
      () => {
        builds++;
        return component;
      },
      'pkg',
    );
    assert.match(host.render(80).join('\n'), /活部件/);
    host.render(80);
    host.render(80);
    assert.equal(builds, 1); // 建厂只发生一次
  });

  it('slots 替换（新函数引用）→ 重判别；注销 → 回空态', () => {
    const { runtime, host } = makeHost();
    const unregister = runtime.slots.register(
      regionSlot('widget'),
      () => new Text('第一版', 1, 0),
      'pkg',
    );
    assert.match(host.render(80).join('\n'), /第一版/);
    unregister();
    runtime.slots.register(
      regionSlot('widget'),
      () => [{ kind: 'markdown', text: '第二版（声明式）' }],
      'pkg',
    );
    // 组件 → 声明式的形态切换也要正确重判别
    assert.match(host.render(80).join('\n'), /第二版（声明式）/);
    runtime.slots = new (runtime.slots.constructor as new () => typeof runtime.slots)();
    assert.deepEqual(host.render(80), []);
  });

  it('部件异常静默（producer 抛错不炸宿主）', () => {
    const { runtime, host } = makeHost();
    runtime.slots.register(
      regionSlot('widget'),
      () => {
        throw new Error('boom');
      },
      'bad-pkg',
    );
    assert.deepEqual(host.render(80), []);
  });
});
