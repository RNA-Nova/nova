/**
 * npm 自愈（packages/npm.ts）行为测试——真实 npm、零 mock：
 * 用临时包目录的可观察结果断言（node_modules 出现与否/返回真假）。
 */

import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, it } from 'node:test';

import { healNpmDependencies } from '../../src/packages/npm.js';

const created: string[] = [];

async function makePkgDir(opts: { lockfile?: boolean; badLockfile?: boolean } = {}): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), 'nova-npm-heal-'));
  created.push(dir);
  await writeFile(join(dir, 'package.json'), JSON.stringify({ name: 'heal-test', version: '0.0.1' }));
  if (opts.lockfile) {
    await writeFile(
      join(dir, 'package-lock.json'),
      JSON.stringify({ name: 'heal-test', version: '0.0.1', lockfileVersion: 3, packages: {} }),
    );
  }
  if (opts.badLockfile) {
    // npm ci 对非法 lockfile 必然失败（ci 分支的可观察探针）
    await writeFile(join(dir, 'package-lock.json'), '{ not valid lockfile');
  }
  return dir;
}

after(async () => {
  for (const dir of created) await rm(dir, { recursive: true, force: true });
});

describe('healNpmDependencies', () => {
  it('无依赖包补装成功（零依赖时 npm 不产生 node_modules——断言返回真）', async () => {
    const dir = await makePkgDir();
    const ok = await healNpmDependencies(dir);
    assert.equal(ok, true);
  });

  it('in-flight 去重：并发两次同目录同结果', async () => {
    const dir = await makePkgDir();
    const [a, b] = await Promise.all([healNpmDependencies(dir), healNpmDependencies(dir)]);
    assert.equal(a, true);
    assert.equal(b, true);
  });

  it('NOVA_OFFLINE 直接跳过（不创建 node_modules）', async () => {
    const dir = await makePkgDir();
    process.env.NOVA_OFFLINE = '1';
    try {
      const ok = await healNpmDependencies(dir);
      assert.equal(ok, false);
      assert.ok(!existsSync(join(dir, 'node_modules')));
    } finally {
      delete process.env.NOVA_OFFLINE;
    }
  });

  it('有合法 lockfile 走 npm ci（可复现安装）', async () => {
    const dir = await makePkgDir({ lockfile: true });
    const ok = await healNpmDependencies(dir);
    assert.equal(ok, true);
  });

  it('坏 lockfile 时 npm ci 失败返回 false（不抛）', async () => {
    const dir = await makePkgDir({ badLockfile: true });
    const ok = await healNpmDependencies(dir);
    assert.equal(ok, false);
  });
});
