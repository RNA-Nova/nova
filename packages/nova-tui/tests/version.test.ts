/**
 * 版本戳测试：node/tsx 形态下 NOVA_VERSION 与包根 package.json 对账
 * （单一事实源纪律——编译态注入值也由构建脚本从同一文件取）。
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';

import { isBunBinary } from '../src/binary.js';
import { NOVA_VERSION } from '../src/version.js';

describe('version', () => {
  it('NOVA_VERSION 与 package.json version 一致（node/tsx 形态）', () => {
    const pkg = JSON.parse(
      readFileSync(new URL('../package.json', import.meta.url), 'utf-8'),
    ) as { version: string };
    assert.equal(NOVA_VERSION, pkg.version);
  });

  it('非编译态 isBunBinary 为 false', () => {
    assert.equal(isBunBinary, false);
  });
});
