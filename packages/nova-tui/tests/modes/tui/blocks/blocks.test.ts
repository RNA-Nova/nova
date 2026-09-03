/**
 * 块注册制测试：schema 校验、builtin 注册、消费点查表/降级、dogfood 覆盖、
 * runtime slotsBootstrap 注入。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  NovaUIRuntime,
  SlotRegistry,
  blockSlot,
  createExtensionUIAPI,
  validateBlock,
  type NovaBlock,
} from 'nova-tui';

import { Text } from '@earendil-works/pi-tui';

import {
  blockToComponent,
  blocksToComponents,
  registerBuiltinBlocks,
} from '../../../../src/modes/tui/blocks/index.js';

function makeSlots(): SlotRegistry {
  const slots = new SlotRegistry();
  registerBuiltinBlocks(createExtensionUIAPI({ slots, source: 'builtin' }));
  return slots;
}

describe('validateBlock', () => {
  it('五种内建合法块通过', () => {
    const blocks: NovaBlock[] = [
      { kind: 'markdown', text: '# hi' },
      { kind: 'code', text: 'x=1', language: 'py' },
      { kind: 'json', data: { a: 1 } },
      { kind: 'table', columns: ['a'], rows: [['1']] },
      { kind: 'diff', hunks: [{ lines: [{ type: 'add', text: '+x' }] }] },
    ];
    for (const block of blocks) {
      assert.equal(validateBlock(block).ok, true, block.kind);
    }
    // json 缺 data 也合法（data 任意）
    assert.equal(validateBlock({ kind: 'json' }).ok, true);
  });

  it('markdown 缺 text / code 错 language 类型 → issues', () => {
    const bad1 = validateBlock({ kind: 'markdown' });
    assert.equal(bad1.ok, false);
    if (!bad1.ok) assert.match(bad1.issues[0]!, /text/);

    const bad2 = validateBlock({ kind: 'code', text: 'x', language: 42 });
    assert.equal(bad2.ok, false);
    if (!bad2.ok) assert.match(bad2.issues[0]!, /language/);
  });

  it('table rows 非数组数组 / diff 行非法 → issues 定位', () => {
    const badTable = validateBlock({ kind: 'table', columns: ['a'], rows: ['not-array'] });
    assert.equal(badTable.ok, false);

    const badDiff = validateBlock({
      kind: 'diff',
      hunks: [{ lines: [{ type: 'weird', text: 'x' }] }],
    });
    assert.equal(badDiff.ok, false);
    if (!badDiff.ok) assert.match(badDiff.issues[0]!, /hunks\[0\]\.lines\[0\]/);
  });

  it('未知 kind 放行（开放集）；非对象/缺 kind 拒绝', () => {
    assert.equal(validateBlock({ kind: 'rna-structure', payload: {} }).ok, true);
    assert.equal(validateBlock('string').ok, false);
    assert.equal(validateBlock({ text: 'x' }).ok, false);
  });
});

describe('块注册制', () => {
  it('registerBuiltinBlocks：五块经同一 register 注册（source=builtin）', () => {
    const slots = makeSlots();
    for (const kind of ['markdown', 'code', 'diff', 'table', 'json']) {
      assert.ok(slots.resolve(blockSlot(kind)), kind);
      assert.equal(slots.sourceOf(blockSlot(kind)), 'builtin');
    }
  });

  it('blockToComponent：合法块产组件；非法块产错误块；未注册 kind 降级提示', () => {
    const slots = makeSlots();
    // 合法块 → Markdown 组件（render 不炸）
    const ok = blockToComponent({ kind: 'markdown', text: '# 标题' }, slots);
    assert.ok(ok.render(80).join('\n').includes('标题'));

    // 非法块 → 错误块（红字 + issues，不炸）
    const invalid = blockToComponent({ kind: 'markdown' } as NovaBlock, slots);
    const invalidOut = invalid.render(80).join('\n');
    assert.match(invalidOut, /非法块/);
    assert.match(invalidOut, /text/);

    // 未注册 kind → 降级 json + 提示行
    const unknown = blockToComponent(
      { kind: 'rna-structure', payload: { x: 1 } } as unknown as NovaBlock,
      slots,
    );
    const unknownOut = unknown.render(80).join('\n');
    assert.match(unknownOut, /未注册块类型: rna-structure/);
    assert.match(unknownOut, /"x": 1/);
  });

  it('dogfood：后注册覆盖 builtin（同键，无内建特权）', () => {
    const slots = makeSlots();
    slots.register(
      blockSlot('markdown'),
      () => {
        throw new Error('自定义适配器被调用');
      },
      'test-pkg',
    );
    assert.equal(slots.sourceOf(blockSlot('markdown')), 'test-pkg');
    assert.throws(() =>
      blockToComponent({ kind: 'markdown', text: 'x' }, slots),
    );
  });

  it('runtime slotsBootstrap：构造注入 builtin（宿主无关层不认识具体贡献）', () => {
    const runtime = new NovaUIRuntime({ slotsBootstrap: registerBuiltinBlocks });
    assert.ok(runtime.slots.resolve(blockSlot('diff')));
    assert.equal(runtime.slots.sourceOf(blockSlot('diff')), 'builtin');

    // 无 bootstrap：空注册表（空态语义）
    const bare = new NovaUIRuntime();
    assert.equal(bare.slots.resolve(blockSlot('diff')), undefined);
  });

  it('blocksToComponents 批量适配', () => {
    const slots = makeSlots();
    const components = blocksToComponents(
      [
        { kind: 'markdown', text: 'a' },
        { kind: 'json', data: 1 },
      ],
      slots,
    );
    assert.equal(components.length, 2);
  });
});

describe('registerBlock 自定义块', () => {
  function makeApi(slots: SlotRegistry) {
    return createExtensionUIAPI({ slots, source: 'test-pkg' });
  }

  it('注册后块经注册表渲染（dogfood：与官方块同通道）', () => {
    const slots = new SlotRegistry();
    registerBuiltinBlocks(createExtensionUIAPI({ slots, source: 'builtin' }));
    makeApi(slots).registerBlock('rna-structure', {
      adapter: (block) =>
        new Text(`RNA: ${(block as Record<string, unknown>).sequence}`, 1, 0),
    });
    assert.equal(slots.sourceOf(blockSlot('rna-structure')), 'test-pkg');

    const block = { kind: 'rna-structure', sequence: 'AUGC' } as unknown as NovaBlock;
    const component = blockToComponent(block, slots);
    assert.match(component.render(80).join('\n'), /RNA: AUGC/);
  });

  it('validate 钩子：issues 非空渲染错误块（适配器不被调用）', () => {
    const slots = new SlotRegistry();
    registerBuiltinBlocks(createExtensionUIAPI({ slots, source: 'builtin' }));
    let adapterCalled = false;
    makeApi(slots).registerBlock('rna-structure', {
      adapter: () => {
        adapterCalled = true;
        return new Text('x', 1, 0);
      },
      validate: (block) =>
        typeof block.sequence === 'string' ? [] : ['sequence 必须是字符串'],
    });

    const bad = blockToComponent({ kind: 'rna-structure' } as unknown as NovaBlock, slots);
    const out = bad.render(80).join('\n');
    assert.match(out, /非法块（kind: rna-structure）/);
    assert.match(out, /sequence 必须是字符串/);
    assert.equal(adapterCalled, false);

    const good = blockToComponent(
      { kind: 'rna-structure', sequence: 'AUGC' } as unknown as NovaBlock,
      slots,
    );
    assert.equal(adapterCalled, true);
    assert.ok(good);
  });

  it('无 validate 的自定义块直接适配；未注册 kind 仍降级 json', () => {
    const slots = new SlotRegistry();
    registerBuiltinBlocks(createExtensionUIAPI({ slots, source: 'builtin' }));
    makeApi(slots).registerBlock('metric', {
      adapter: (block) => new Text(`M:${(block as Record<string, unknown>).v}`, 1, 0),
    });
    assert.match(
      blockToComponent({ kind: 'metric', v: 1 } as unknown as NovaBlock, slots)
        .render(80)
        .join('\n'),
      /M:1/,
    );
    // 未注册 kind：降级提示不受影响
    assert.match(
      blockToComponent({ kind: 'unknown-kind' } as unknown as NovaBlock, slots)
        .render(80)
        .join('\n'),
      /未注册块类型/,
    );
  });
});
