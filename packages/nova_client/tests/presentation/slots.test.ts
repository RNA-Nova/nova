import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { SlotRegistry, entrySlot, regionSlot, toolSlot } from '../../src/presentation/slots.js';

describe('SlotRegistry', () => {
  it('register/resolve 基本往返', () => {
    const slots = new SlotRegistry();
    const render = (input: unknown) => [{ kind: 'markdown' as const, text: String(input) }];
    slots.register(toolSlot('bash'), render, 'pkg-a');
    assert.equal(slots.resolve(toolSlot('bash')), render);
    assert.equal(slots.resolveToolRenderer('bash'), render);
    assert.equal(slots.resolveToolRenderer('missing'), undefined);
  });

  it('后注册覆盖同键，来源随之更新', () => {
    const slots = new SlotRegistry();
    slots.register(toolSlot('bash'), () => [], 'pkg-a');
    slots.register(toolSlot('bash'), () => [], 'pkg-b');
    assert.equal(slots.sourceOf(toolSlot('bash')), 'pkg-b');
  });

  it('注销函数只注销自己（不删后注册者的覆盖）', () => {
    const slots = new SlotRegistry();
    const offA = slots.register(toolSlot('bash'), () => [], 'pkg-a');
    slots.register(toolSlot('bash'), () => [], 'pkg-b');
    offA();
    // A 注销不应删掉 B 的覆盖
    assert.equal(slots.sourceOf(toolSlot('bash')), 'pkg-b');
  });

  it('list 返回全部注册记录', () => {
    const slots = new SlotRegistry();
    slots.register(toolSlot('bash'), () => [], 'pkg-a');
    slots.register(entrySlot('assistant'), () => [], 'builtin');
    slots.register(regionSlot('footer'), () => [], 'pkg-c');
    const keys = slots.list().map((r) => r.key);
    assert.deepEqual(keys.sort(), ['entry:assistant', 'region:footer', 'tool:bash']);
  });
});
