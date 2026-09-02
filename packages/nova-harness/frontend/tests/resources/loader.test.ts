import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, stat, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, it } from 'node:test';

import type { PackageUIAssets } from '../../src/resources/types.js';
import { SlotRegistry, dialogSlot, regionSlot, toolSlot } from '../../src/presentation/slots.js';
import type { NovaBlock } from '../../src/presentation/blocks.js';
import { loadUIAssets } from '../../src/resources/loader.js';
import { partitionByTrust } from '../../src/resources/trust.js';

const created: string[] = [];

/** 从加载结果筛错误诊断（失败即 error 型 diagnostic）。 */
function errorsOf(result: { diagnostics: { type: string }[] }) {
  return result.diagnostics.filter((d) => d.type === 'error');
}

/** 造一个带 frontend/tui/tools 的临时包目录，返回 assets 描述。 */
async function makePkg(
  name: string,
  scope: 'user' | 'project',
  renderers: Record<string, string>,
): Promise<PackageUIAssets> {
  const dir = await mkdtemp(join(tmpdir(), 'nova-loader-test-'));
  created.push(dir);
  const renderersDir = join(dir, 'frontend', 'tui', 'tools');
  await mkdir(renderersDir, { recursive: true });
  const paths = new Map<string, string>();
  for (const [toolName, source] of Object.entries(renderers)) {
    const filePath = join(renderersDir, `${toolName}.ts`);
    await writeFile(filePath, source);
    paths.set(toolName, filePath);
  }
  return {
    packageName: name,
    scope,
    installPath: dir,
    renderers: paths,
    themes: new Map(),
    needsNpmInstall: false,
  };
}

after(async () => {
  for (const dir of created) await rm(dir, { recursive: true, force: true });
});

const SIMPLE_RENDERER = `
export default function render(input: { toolName: string }) {
  return [{ kind: 'markdown', text: 'rendered:' + input.toolName }];
}
`;

describe('loadUIAssets', () => {
  it('jiti 加载 .ts 渲染器并注册到 tool:<name> slot', async () => {
    const assets = [await makePkg('pkg-a', 'user', { bash: SIMPLE_RENDERER })];
    const slots = new SlotRegistry();
    const result = await loadUIAssets(assets, slots);
    assert.deepEqual(errorsOf(result), []);
    assert.equal(result.loaded.length, 1);
    assert.equal(result.loaded[0]?.name, 'bash');
    assert.equal(result.loaded[0]?.source, 'pkg-a');
    assert.equal(typeof result.loaded[0]?.durationMs, 'number');

    const renderer = slots.resolveToolRenderer('bash');
    assert.ok(renderer);
    const blocks = renderer({ toolName: 'bash', status: 'done' }) as NovaBlock[];
    assert.deepEqual(blocks, [{ kind: 'markdown', text: 'rendered:bash' }]);
    assert.equal(slots.sourceOf(toolSlot('bash')), 'pkg-a');
  });

  it('nova-client 别名：渲染器可 import 本包 blocks 模块', async () => {
    const aliased = `
import { detailsOf } from 'nova-client/presentation/blocks.js';
export default function render(input) {
  const d = detailsOf(input);
  return [{ kind: 'code', text: String(d.command ?? '') }];
}
`;
    const assets = [await makePkg('pkg-b', 'user', { bash: aliased })];
    const slots = new SlotRegistry();
    const result = await loadUIAssets(assets, slots);
    assert.deepEqual(errorsOf(result), []);
    const renderer = slots.resolveToolRenderer('bash');
    const blocks = renderer?.({
      toolName: 'bash',
      status: 'done',
      result: { details: { command: 'ls -la' } },
    });
    assert.deepEqual(blocks, [{ kind: 'code', text: 'ls -la' }]);
  });

  it('trust 门控：项目不被信任时 project 级包不加载', async () => {
    const assets = [
      await makePkg('pkg-user', 'user', { bash: SIMPLE_RENDERER }),
      await makePkg('pkg-project', 'project', { edit: SIMPLE_RENDERER }),
    ];
    // 门控已上移编排层：loader 是纯加载器，门控语义经 partitionByTrust 测试
    // （见 packages/assets.test.ts）；这里验证 loader 对传入资产照单全收
    const slots = new SlotRegistry();
    const { allowed, skipped } = partitionByTrust(assets, false);
    assert.deepEqual(skipped, ['pkg-project']);
    assert.deepEqual(allowed.map((a) => a.packageName), ['pkg-user']);

    const result = await loadUIAssets(allowed, slots);
    assert.deepEqual(errorsOf(result), []);
    assert.ok(slots.resolveToolRenderer('bash'));
    assert.equal(slots.resolveToolRenderer('edit'), undefined);
  });

  it('坏渲染器记录失败，不阻断其他包', async () => {
    const assets = [
      await makePkg('pkg-bad', 'user', { broken: 'throw new Error("语法就错了"()' }),
      await makePkg('pkg-good', 'user', { read: SIMPLE_RENDERER }),
    ];
    const slots = new SlotRegistry();
    const result = await loadUIAssets(assets, slots);
    assert.equal(errorsOf(result).length, 1);
    assert.match(errorsOf(result)[0]?.message ?? '', /pkg-bad\/broken/);
    assert.ok(slots.resolveToolRenderer('read'));
  });

  it('默认导出不是函数 → 失败记录', async () => {
    const assets = [await makePkg('pkg-c', 'user', { weird: 'export default 42;' })];
    const slots = new SlotRegistry();
    const result = await loadUIAssets(assets, slots);
    assert.equal(errorsOf(result).length, 1);
    assert.match(errorsOf(result)[0]?.message ?? '', /渲染函数/);
  });

  it('preview 命名导出被收集（无导出的渲染器不影响加载）', async () => {
    const withPreview = `
export default function render() { return []; }
export async function preview(args, cwd) { return { patch: 'x', path: args.path, cwd }; }
`;
    const assets = [
      await makePkg('pkg-prev', 'user', { edit: withPreview, bash: SIMPLE_RENDERER }),
    ];
    const slots = new SlotRegistry();
    const result = await loadUIAssets(assets, slots);
    assert.deepEqual(errorsOf(result), []);

    // edit：render + preview 双双就位
    assert.ok(slots.resolveToolRenderer('edit'));
    const compute = slots.resolveToolPreview('edit');
    assert.ok(compute);
    const out = (await compute({ path: 'a.ts' }, '/tmp')) as { path: string };
    assert.equal(out.path, 'a.ts');

    // bash：无 preview 导出 → 解析为 undefined，render 照常
    assert.ok(slots.resolveToolRenderer('bash'));
    assert.equal(slots.resolveToolPreview('bash'), undefined);
  });

  it('结果缓存：mtime 未变直接注册缓存产物（jiti 不重跑）', async () => {
    const assets = [await makePkg('pkg-cache', 'user', { bash: SIMPLE_RENDERER })];
    const filePath = [...assets[0]!.renderers.values()][0]!;
    const slots1 = new SlotRegistry();
    const first = await loadUIAssets(assets, slots1);
    assert.deepEqual(errorsOf(first), []);

    // 把文件改成坏语法但**保持 mtime**——若 jiti 重跑必失败；
    // 加载仍成功即证明走了缓存。
    // 注意：utimes 用浮点秒传参（Date 只存整数毫秒会截断亚毫秒，
    // 导致设回的 mtime 与 stat 的浮点 mtimeMs 不相等）
    const mtime = (await stat(filePath)).mtimeMs;
    await writeFile(filePath, 'this is not valid typescript (((');
    await utimes(filePath, mtime / 1000, mtime / 1000);

    const slots2 = new SlotRegistry();
    const second = await loadUIAssets(assets, slots2);
    assert.deepEqual(errorsOf(second), []);
    assert.equal(second.loaded.length, 1);
    assert.equal(second.loaded[0]?.name, 'bash');
    assert.equal(second.loaded[0]?.source, 'pkg-cache');
    assert.ok(slots2.resolveToolRenderer('bash'));
  });

  it('结果缓存：mtime 变化自动失效重载（内容变了拿到新代码）', async () => {
    const assets = [await makePkg('pkg-cache2', 'user', { bash: SIMPLE_RENDERER })];
    const filePath = [...assets[0]!.renderers.values()][0]!;
    const slots1 = new SlotRegistry();
    await loadUIAssets(assets, slots1);

    // 改成坏语法并**推进 mtime**——缓存失效，jiti 重跑暴露坏语法
    await writeFile(filePath, 'this is not valid typescript (((');
    const future = new Date(Date.now() + 60_000);
    await utimes(filePath, future, future);

    const slots2 = new SlotRegistry();
    const second = await loadUIAssets(assets, slots2);
    assert.equal(errorsOf(second).length, 1);
    assert.match(errorsOf(second)[0]?.message ?? '', /pkg-cache2\/bash/);
  });

  it('force 绕过缓存（reload 场景：mtime 未变也强制重载）', async () => {
    const assets = [await makePkg('pkg-force', 'user', { bash: SIMPLE_RENDERER })];
    const filePath = [...assets[0]!.renderers.values()][0]!;
    const slots1 = new SlotRegistry();
    await loadUIAssets(assets, slots1);

    // 改成坏语法保持 mtime（浮点秒防 Date 截断）：缓存路径能过，force 路径必须暴露
    const mtime = (await stat(filePath)).mtimeMs;
    await writeFile(filePath, 'this is not valid typescript (((');
    await utimes(filePath, mtime / 1000, mtime / 1000);

    const slots2 = new SlotRegistry();
    const forced = await loadUIAssets(assets, slots2, { force: true });
    assert.equal(errorsOf(forced).length, 1);
    assert.match(errorsOf(forced)[0]?.message ?? '', /pkg-force\/bash/);
  });
});

describe('loadUIAssets · ui/index.ts 扩展入口', () => {
  /** 造带 frontend/tui/index.ts 的包（renderers 可选）。 */
  async function makeEntryPkg(
    name: string,
    entry: string,
    renderers: Record<string, string> = {},
  ): Promise<PackageUIAssets> {
    const assets = await makePkg(name, 'user', renderers);
    const entryPath = join(assets.installPath, 'frontend', 'tui', 'index.ts');
    await writeFile(entryPath, entry);
    assets.extensionEntry = entryPath;
    return assets;
  }

  it('工厂经 ExtensionUIAPI 注册渲染器与区域部件', async () => {
    const entry = `
export default function (api) {
  api.registerRenderer('deploy', (input) => [{ kind: 'markdown', text: 'deploy:' + input.status }]);
  api.registerRegion('footer', (ctx) => [{ kind: 'markdown', text: 'cwd:' + ctx.cwd }]);
}
`;
    const assets = [await makeEntryPkg('pkg-ext', entry)];
    const slots = new SlotRegistry();
    const result = await loadUIAssets(assets, slots);
    assert.deepEqual(errorsOf(result), []);
    assert.equal(result.loaded.length, 1);
    assert.equal(result.loaded[0]?.name, 'tui/index');

    const renderer = slots.resolveToolRenderer('deploy');
    assert.ok(renderer);
    assert.deepEqual(renderer({ toolName: 'deploy', status: 'done' }), [
      { kind: 'markdown', text: 'deploy:done' },
    ]);
    assert.equal(slots.sourceOf(toolSlot('deploy')), 'pkg-ext');

    const region = slots.resolve(regionSlot('footer'));
    assert.ok(region);
    assert.deepEqual(region({ cwd: '/tmp/x' }), [{ kind: 'markdown', text: 'cwd:/tmp/x' }]);
    assert.equal(slots.sourceOf(regionSlot('footer')), 'pkg-ext');
  });

  it('默认导出非工厂函数 → 错误诊断', async () => {
    const assets = [await makeEntryPkg('pkg-bad-entry', 'export default 42;')];
    const slots = new SlotRegistry();
    const result = await loadUIAssets(assets, slots);
    assert.equal(errorsOf(result).length, 1);
    assert.match(errorsOf(result)[0]?.message ?? '', /工厂函数/);
  });

  it('同包 index 覆盖 renderers 文件（同源无碰撞）；跨包覆盖 → 碰撞诊断', async () => {
    // 同包：renderers/bash.ts + index.ts 都注册 bash——同源覆盖不算碰撞
    const samePkg = [
      await makeEntryPkg(
        'pkg-same',
        `export default function (api) { api.registerRenderer('bash', () => [{ kind: 'markdown', text: 'from-index' }]); }`,
        { bash: SIMPLE_RENDERER },
      ),
    ];
    const slots1 = new SlotRegistry();
    const result1 = await loadUIAssets(samePkg, slots1);
    assert.deepEqual(errorsOf(result1), []);
    assert.deepEqual(result1.diagnostics.filter((d) => d.type === 'collision'), []);
    // index 后执行：覆盖文件约定
    assert.deepEqual(slots1.resolveToolRenderer('bash')?.({ toolName: 'bash', status: 'done' }), [
      { kind: 'markdown', text: 'from-index' },
    ]);

    // 跨包：pkg-a 与 pkg-b 都注册 bash——后注册者赢 + 碰撞诊断
    const crossPkg = [
      await makePkg('pkg-a', 'user', { bash: SIMPLE_RENDERER }),
      await makeEntryPkg(
        'pkg-b',
        `export default function (api) { api.registerRenderer('bash', () => [{ kind: 'markdown', text: 'from-b' }]); }`,
      ),
    ];
    const slots2 = new SlotRegistry();
    const result2 = await loadUIAssets(crossPkg, slots2);
    const collisions = result2.diagnostics.filter((d) => d.type === 'collision');
    assert.equal(collisions.length, 1);
    assert.match(collisions[0]?.message ?? '', /pkg-a → pkg-b/);
    assert.equal(collisions[0]?.collision?.kind, 'renderer');
    assert.equal(collisions[0]?.collision?.name, 'bash');
  });

  it('index.ts 缓存：mtime 未变零 jiti 重编译，工厂仍重新执行（重新注册）', async () => {
    const assets = [
      await makeEntryPkg(
        'pkg-entry-cache',
        `export default function (api) { api.registerRegion('footer', () => [{ kind: 'json', data: 1 }]); }`,
      ),
    ];
    const entryPath = assets[0]!.extensionEntry!;
    const slots1 = new SlotRegistry();
    await loadUIAssets(assets, slots1);
    assert.ok(slots1.resolve(regionSlot('footer')));

    // 坏语法但保持 mtime：命中缓存（jiti 不重跑）；工厂在新 slots 上重新执行
    const mtime = (await stat(entryPath)).mtimeMs;
    await writeFile(entryPath, 'not valid ts (((');
    await utimes(entryPath, mtime / 1000, mtime / 1000);

    const slots2 = new SlotRegistry();
    const second = await loadUIAssets(assets, slots2);
    assert.deepEqual(errorsOf(second), []);
    assert.ok(slots2.resolve(regionSlot('footer'))); // 工厂重执行注册成功
  });

  it('包内主题资产（frontend/themes/*.json）：校验收集 + 碰撞诊断 + 坏文件诊断', async () => {
    const goodTheme = JSON.stringify({
      name: 'ocean',
      colors: Object.fromEntries(
        // 46 必需 token 全填 hex（校验通过的最小集）
        (await import('../../src/presentation/theme-json.js')).REQUIRED_COLOR_TOKENS.map((t) => [
          t,
          '#112233',
        ]),
      ),
    });
    const mkThemes = async (pkgName: string, files: Record<string, string>) => {
      const assets = await makePkg(pkgName, 'user', {}); // 不带渲染器——只测主题
      const themesDir = join(assets.installPath, 'frontend', 'themes');
      await mkdir(themesDir, { recursive: true });
      for (const [file, content] of Object.entries(files)) {
        await writeFile(join(themesDir, file), content);
        assets.themes.set(file.replace(/\.json$/, ''), join(themesDir, file));
      }
      return assets;
    };

    const assets = [
      await mkThemes('pkg-x', { 'ocean.json': goodTheme }),
      await mkThemes('pkg-y', {
        'ocean.json': goodTheme, // 同名撞 pkg-x → 碰撞
        'broken.json': '{ not json', // 坏文件 → 错误诊断
      }),
    ];
    const slots = new SlotRegistry();
    const result = await loadUIAssets(assets, slots);

    assert.ok(result.themes.has('ocean'));
    assert.equal(result.themes.get('ocean')?.name, 'ocean');
    const collisions = result.diagnostics.filter((d) => d.type === 'collision');
    assert.equal(collisions.length, 1);
    assert.match(collisions[0]?.message ?? '', /pkg-x → pkg-y/);
    const errors = errorsOf(result);
    assert.equal(errors.length, 1);
    assert.match(errors[0]?.message ?? '', /broken/);
    // loaded 记录含 theme: 条目
    assert.ok(result.loaded.some((entry) => entry.name === 'theme:ocean'));
  });
});

describe('loadUIAssets · 散养资产（dialogs 文件约定 + 优先级碰撞）', () => {
  /** 造散养根形态的 assets（tools/dialogs 文件约定 + 可选 index.ts）。 */
  async function makeLoose(
    scope: 'user' | 'project',
    layout: Record<string, string>,
  ): Promise<PackageUIAssets> {
    const dir = await mkdtemp(join(tmpdir(), 'nova-loose-loader-test-'));
    created.push(dir);
    const renderers = new Map<string, string>();
    const dialogs = new Map<string, string>();
    let extensionEntry: string | undefined;
    for (const [rel, source] of Object.entries(layout)) {
      const filePath = join(dir, rel);
      await mkdir(join(filePath, '..'), { recursive: true });
      await writeFile(filePath, source);
      if (rel.startsWith('tools/')) renderers.set(rel.slice(6, -3), filePath);
      else if (rel.startsWith('dialogs/')) dialogs.set(rel.slice(8, -3), filePath);
      else if (rel === 'index.ts') extensionEntry = filePath;
    }
    return {
      packageName: scope, // 散养无包名——scope 即来源标签
      scope,
      installPath: dir,
      renderers,
      dialogs,
      extensionEntry,
      themes: new Map(),
      needsNpmInstall: false,
    };
  }

  it('dialogs/*.ts 默认导出注册为 dialog:<name> slot（触发 onDialogChange）', async () => {
    const assets = [
      await makeLoose('user', {
        'dialogs/ask.ts': `
export default function ask(env, params, done) { return { kind: 'ask', params }; }
`,
      }),
    ];
    const slots = new SlotRegistry();
    let dialogChanges = 0;
    const result = await loadUIAssets(assets, slots, {
      onDialogChange: () => {
        dialogChanges += 1;
      },
    });
    assert.deepEqual(errorsOf(result), []);
    assert.ok(result.loaded.some((entry) => entry.name === 'dialog:ask' && entry.source === 'user'));

    const factory = slots.resolve(dialogSlot('ask'));
    assert.ok(factory);
    assert.equal(dialogChanges, 1); // 注册即触发能力重宣告
  });

  it('对话框默认导出非函数 → 错误诊断，不阻断渲染器', async () => {
    const assets = [
      await makeLoose('user', {
        'dialogs/bad.ts': 'export default 42;',
        'tools/bash.ts': SIMPLE_RENDERER,
      }),
    ];
    const slots = new SlotRegistry();
    const result = await loadUIAssets(assets, slots);
    assert.equal(errorsOf(result).length, 1);
    assert.match(errorsOf(result)[0]?.message ?? '', /对话框加载失败（user\/bad）/);
    assert.ok(slots.resolveToolRenderer('bash'));
  });

  it('index.ts 后于 dialogs 文件约定执行（同源可编程覆盖）', async () => {
    const assets = [
      await makeLoose('project', {
        'dialogs/ask.ts': `export default function () { return 'from-file'; }`,
        'index.ts': `
export default function (api) { api.registerDialog('ask', () => 'from-index'); }
`,
      }),
    ];
    const slots = new SlotRegistry();
    const result = await loadUIAssets(assets, slots);
    assert.deepEqual(errorsOf(result), []);
    // 同源覆盖不算碰撞
    assert.deepEqual(result.diagnostics.filter((d) => d.type === 'collision'), []);
    const factory = slots.resolve(dialogSlot('ask')) as unknown as () => string;
    assert.equal(factory(), 'from-index');
  });

  it('优先级碰撞：project 散养 > user 散养 > package（注册顺序即覆盖优先级）', async () => {
    const pkg = await makePkg('pkg-a', 'user', { bash: SIMPLE_RENDERER });
    const userLoose = await makeLoose('user', {
      'tools/bash.ts': `export default function () { return [{ kind: 'markdown', text: 'loose-user' }]; }`,
    });
    const projectLoose = await makeLoose('project', {
      'tools/bash.ts': `export default function () { return [{ kind: 'markdown', text: 'loose-project' }]; }`,
    });
    // 编排顺序（runtime.refreshPackages 同款）：包 → user 散养 → project 散养
    const slots = new SlotRegistry();
    const result = await loadUIAssets([pkg, userLoose, projectLoose], slots);

    const collisions = result.diagnostics.filter((d) => d.type === 'collision');
    assert.equal(collisions.length, 2); // pkg→user、user→project 各一次
    assert.match(collisions[0]?.message ?? '', /pkg-a → user/);
    assert.match(collisions[1]?.message ?? '', /user → project/);
    const renderer = slots.resolveToolRenderer('bash');
    assert.deepEqual(renderer?.({ toolName: 'bash', status: 'done' }), [
      { kind: 'markdown', text: 'loose-project' },
    ]);
    assert.equal(slots.sourceOf(toolSlot('bash')), 'project');
  });
});
