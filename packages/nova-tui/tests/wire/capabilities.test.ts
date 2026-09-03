import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { NOVA_CONTRACT_MAJOR } from '../../src/protocol/nova-wire.gen.js';
import { CapabilitySet, checkContractVersion } from '../../src/wire/capabilities.js';

describe('checkContractVersion（R6：major/minor 语义）', () => {
  it('major 一致 → 放行（minor 任意差都兼容）', () => {
    checkContractVersion({ contractVersionMajor: NOVA_CONTRACT_MAJOR, contractVersionMinor: 0 });
    checkContractVersion({ contractVersionMajor: NOVA_CONTRACT_MAJOR, contractVersionMinor: 99 });
  });

  it('major 不等 → 硬拒（响亮失败）', () => {
    assert.throws(
      () => checkContractVersion({ contractVersionMajor: NOVA_CONTRACT_MAJOR + 1, contractVersionMinor: 0 }),
      /不兼容/,
    );
  });

  it('major 缺失（旧后端/非 Nova 后端）→ 硬拒', () => {
    assert.throws(() => checkContractVersion({}), /不兼容/);
  });
});

describe('CapabilitySet', () => {
  it('域/方法两级查询，未宣告即不可用', () => {
    const caps = new CapabilitySet({
      capabilities: { domains: ['session', 'model'], methods: ['prompt', 'abort'] },
    });
    assert.equal(caps.hasDomain('session'), true);
    assert.equal(caps.hasDomain('package'), false);
    assert.equal(caps.hasMethod('prompt'), true);
    assert.equal(caps.hasMethod('pkgList'), false);
  });

  it('握手缺 capabilities 字段时一切不可用（降级而非崩溃）', () => {
    const caps = new CapabilitySet({});
    assert.equal(caps.hasDomain('session'), false);
    assert.equal(caps.hasMethod('prompt'), false);
  });
});
