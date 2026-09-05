import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, it } from 'node:test';

import { discoverLooseAssets, discoverLooseUIAssets, discoverUIAssets } from '../../src/resources/discovery.js';
import type { InstalledPackageInfo } from '../../src/packages/registry.js';

const created: string[] = [];

async function makePackage(layout: Record<string, string>): Promise<InstalledPackageInfo> {
  const dir = await mkdtemp(join(tmpdir(), 'nova-assets-test-'));
  created.push(dir);
  for (const [rel, content] of Object.entries(layout)) {
    const path = join(dir, rel);
    await mkdir(join(path, '..'), { recursive: true });
    await writeFile(path, content);
  }
  return {
    identity: dir,
    name: 'test-pkg',
    version: '0.1.0',
    description: '',
    installPath: dir,
    scope: 'user',
  };
}

after(async () => {
  for (const dir of created) await rm(dir, { recursive: true, force: true });
});

describe('discoverUIAssets', () => {
  it('无 frontend/ 段 → null', async () => {
    const pkg = await makePackage({ 'pyproject.toml': '[project]\nname = "x"\n' });
    assert.equal(await discoverUIAssets(pkg), null);
  });

  it('发现 tui/tools/*.ts（文件名即工具名）与扩展入口', async () => {
    const pkg = await makePackage({
      'frontend/tui/tools/bash.ts': 'export default function () { return []; }',
      'frontend/tui/tools/edit.ts': 'export default function () { return []; }',
      'frontend/tui/tools/notes.txt': '不是渲染器',
      'frontend/tui/index.ts': 'export default function () {}',
    });
    const assets = await discoverUIAssets(pkg);
    assert.ok(assets);
    assert.deepEqual([...assets.renderers.keys()].sort(), ['bash', 'edit']);
    assert.match(assets.extensionEntry ?? '', /frontend[\\/]tui[\\/]index\.ts$/);
    assert.match(assets.renderers.get('bash') ?? '', /frontend[\\/]tui[\\/]tools[\\/]bash\.ts$/);
  });

  it('user_tools 同族发现；tools 同名覆盖 user_tools', async () => {
    const pkg = await makePackage({
      'frontend/tui/user_tools/bash.ts': 'export default function () { return "user"; }',
      'frontend/tui/tools/bash.ts': 'export default function () { return "tool"; }',
      'frontend/tui/user_tools/extra.ts': 'export default function () { return []; }',
    });
    const assets = await discoverUIAssets(pkg);
    assert.ok(assets);
    assert.deepEqual([...assets.renderers.keys()].sort(), ['bash', 'extra']);
    assert.match(assets.renderers.get('bash') ?? '', /tools[\\/]bash\.ts$/); // tools 胜出
  });

  it('frontend/package.json 无 node_modules → needsNpmInstall', async () => {
    const pkg = await makePackage({
      'frontend/tui/tools/bash.ts': 'export default function () { return []; }',
      'frontend/package.json': '{ "name": "test-pkg", "dependencies": { "pretty-ms": "^9.2.0" } }',
    });
    const assets = await discoverUIAssets(pkg);
    assert.equal(assets?.needsNpmInstall, true);
  });

  it('A 型包根遗留 package.json 不触发 npm 自愈（无双轨——半区清单唯一）', async () => {
    const pkg = await makePackage({
      'frontend/tui/tools/bash.ts': 'export default function () { return []; }',
      'package.json': '{ "name": "test-pkg", "dependencies": {} }',
    });
    const assets = await discoverUIAssets(pkg);
    assert.equal(assets?.needsNpmInstall, false); // A 型只认 frontend/package.json
  });

  it('无 package.json（纯源码 frontend/）→ 不需要 npm 自愈', async () => {
    const pkg = await makePackage({
      'frontend/tui/tools/bash.ts': 'export default function () { return []; }',
    });
    const assets = await discoverUIAssets(pkg);
    assert.equal(assets?.needsNpmInstall, false);
  });

  it('B 型：包根即前端半区（tui/ 直接在根下）', async () => {
    const pkg = await makePackage({
      'tui/tools/bash.ts': 'export default function () { return []; }',
      'tui/index.ts': 'export default function () {}',
      'package.json': '{ "name": "test-pkg", "dependencies": { "pretty-ms": "^9.2.0" } }',
    });
    const assets = await discoverUIAssets(pkg);
    assert.ok(assets);
    assert.deepEqual([...assets.renderers.keys()], ['bash']);
    assert.match(assets.extensionEntry ?? '', /tui[\\/]index\.ts$/);
    assert.equal(assets.needsNpmInstall, true); // 根 package.json（B 型身份证）即半区清单
  });

  it('主题资产：frontend/themes/*.json（宿主无关）', async () => {
    const pkg = await makePackage({
      'frontend/themes/dark-x.json': '{ "name": "dark-x", "colors": {} }',
      'frontend/themes/notes.txt': '不是主题',
    });
    const assets = await discoverUIAssets(pkg);
    assert.ok(assets);
    assert.deepEqual([...assets.themes.keys()], ['dark-x']);
  });
});

// ---------------------------------------------------------------------------
// 散养资产根（前后端分治 §9——frontend/<host>/ 直挂资产，无包身份）
// ---------------------------------------------------------------------------

/** 造一个散养根目录（layout 相对根），返回绝对路径。 */
async function makeLooseRoot(layout: Record<string, string>): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), 'nova-loose-test-'));
  created.push(dir);
  for (const [rel, content] of Object.entries(layout)) {
    const path = join(dir, rel);
    await mkdir(join(path, '..'), { recursive: true });
    await writeFile(path, content);
  }
  return dir;
}

describe('discoverLooseUIAssets（散养根）', () => {
  it('根不存在 → null（不创建）', async () => {
    assert.equal(await discoverLooseUIAssets(join(tmpdir(), 'nova-loose-nonexistent'), 'user'), null);
  });

  it('识别 tools/dialogs/index.ts 三类资产', async () => {
    const root = await makeLooseRoot({
      'tools/bash.ts': 'export default function () { return []; }',
      'tools/bash.test.ts': '测试文件不收',
      'dialogs/ask.ts': 'export default function () {}',
      'index.ts': 'export default function () {}',
      'notes.txt': '非 ts 不收',
    });
    const assets = await discoverLooseUIAssets(root, 'user');
    assert.ok(assets);
    assert.deepEqual([...assets.renderers.keys()], ['bash']);
    assert.deepEqual([...(assets.dialogs?.keys() ?? [])], ['ask']);
    assert.match(assets.extensionEntry ?? '', /index\.ts$/);
    assert.equal(assets.packageName, 'user'); // 散养无包名——scope 即来源标签
    assert.equal(assets.scope, 'user');
    assert.equal(assets.needsNpmInstall, false);
    assert.equal(assets.themes.size, 0); // 散养 themes 不走本管线
  });

  it('空根（无可加载内容）→ null', async () => {
    const root = await makeLooseRoot({ 'readme.md': '空' });
    assert.equal(await discoverLooseUIAssets(root, 'user'), null);
  });

  it('根下 package.json 不触发 npm 自愈（配置目录不跑 npm）', async () => {
    const root = await makeLooseRoot({
      'tools/bash.ts': 'export default function () { return []; }',
      'package.json': '{ "name": "loose" }',
    });
    const assets = await discoverLooseUIAssets(root, 'project');
    assert.equal(assets?.needsNpmInstall, false);
  });
});

describe('discoverLooseAssets（双根编排 + trust 门）', () => {
  it('user 恒收、project 可信时收在后（顺序即优先级）', async () => {
    const userRoot = await makeLooseRoot({ 'tools/a.ts': 'export default function () {}' });
    const projectRoot = await makeLooseRoot({ 'tools/b.ts': 'export default function () {}' });

    const loose = await discoverLooseAssets({ userRoot, projectRoot, trusted: true });

    assert.deepEqual(
      loose.map((a) => a.scope),
      ['user', 'project'], // project 散养最后注册——覆盖优先级最高
    );
  });

  it('项目未信任：project 根不收（发现门在扫描之前）', async () => {
    const userRoot = await makeLooseRoot({ 'tools/a.ts': 'export default function () {}' });
    const projectRoot = await makeLooseRoot({ 'tools/b.ts': 'export default function () {}' });

    const loose = await discoverLooseAssets({ userRoot, projectRoot, trusted: false });

    assert.deepEqual(
      loose.map((a) => a.scope),
      ['user'],
    );
  });

  it('双根均无资产 → 空列表', async () => {
    const userRoot = await makeLooseRoot({});
    const projectRoot = await makeLooseRoot({});
    const loose = await discoverLooseAssets({ userRoot, projectRoot, trusted: true });
    assert.deepEqual(loose, []);
  });
});


describe('needsNpmInstall 判定', () => {
  const RENDERER = 'export default function () { return []; }\n';

  it('零运行时 dependencies 的清单不触发自愈（防死循环：零依赖 npm 不产生 node_modules）', async () => {
    const pkg = await makePackage({
      'frontend/tui/tools/bash.ts': RENDERER,
      'frontend/package.json': JSON.stringify({ name: 'x', version: '0.0.1' }),
    });
    const assets = await discoverUIAssets(pkg);
    assert.equal(assets?.needsNpmInstall, false);
    // 有清单即有 npmDir（自愈工作目录），只是本轮不需要
    assert.ok(assets?.npmDir);
  });

  it('有运行时 dependencies + 缺 node_modules → 需自愈', async () => {
    const pkg = await makePackage({
      'frontend/tui/tools/bash.ts': RENDERER,
      'frontend/package.json': JSON.stringify({
        name: 'x',
        version: '0.0.1',
        dependencies: { 'pretty-ms': '^9.2.0' },
      }),
    });
    const assets = await discoverUIAssets(pkg);
    assert.equal(assets?.needsNpmInstall, true);
    assert.ok(assets?.npmDir?.endsWith('frontend'));
  });

  it('有依赖但 node_modules 已在 → 不自愈', async () => {
    const pkg = await makePackage({
      'frontend/tui/tools/bash.ts': RENDERER,
      'frontend/package.json': JSON.stringify({
        name: 'x',
        version: '0.0.1',
        dependencies: { 'pretty-ms': '^9.2.0' },
      }),
      'frontend/node_modules/.keep': '',
    });
    const assets = await discoverUIAssets(pkg);
    assert.equal(assets?.needsNpmInstall, false);
  });
});
