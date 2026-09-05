/**
 * 呈现资源发现（resources/ 子系统）。
 *
 * 包的前端半区 ``frontend/``（纯源码与资产目录，零清单零产物）：
 * - ``frontend/themes/*.json``       主题资产（宿主无关数据）；
 * - ``frontend/<host>/``             宿主段（当前仅 ``tui``；M3 增 ``web/``）：
 *   - ``index.ts``                   扩展入口（工厂拿 ExtensionUIAPI——编程式注册）;
 *   - ``tools/<name>.ts``            工具渲染器（文件名即工具名；返回块或组件）；
 *   - ``user_tools/<name>.ts``       用户工具渲染器（同族规则）；
 *   - ``extensions/``、``lib/``      组织域（不参与发现——经 index.ts 引入）。
 *
 * 散养根（前后端分治 §9 新增扫描能力——无包身份的前端域直挂资产）：
 * - user 级 ``~/.nova/agent/frontend/tui/``（永远可信）；
 * - project 级 ``<cwd>/.nova/frontend/tui/``（trust 门控归编排层——未信任
 *   不 stat 不 import，见 runtime.refreshPackages）；
 * - 识别 ``tools/<tool>.ts``（工具渲染器）、``dialogs/<name>.ts``（自定义
 *   对话框——散养专属文件约定；包的对话框走 index.ts 编程式注册）与
 *   ``index.ts``（扩展入口）；
 * - 散养 themes 不经本管线：user 级归 theme.ts 的目录约定（customThemesDir），
 *   project 级主题暂无消费通道（预留）。
 *
 * 目录纪律（镜像约定）：前端段镜像后端资源类型目录（tools/user_tools/
 * extensions）——位置即语义，后端 taxonomy 即前端 taxonomy。渲染器目录是
 * 纯发现域（一文件一工具、默认导出渲染函数，可选 ``preview`` 命名导出）；
 * 辅助模块归 ``lib/``、测试归半区 ``tests/``——发现跳过 ``*.test.ts`` 防御放错。
 */

import { existsSync } from 'node:fs';
import { readdir } from 'node:fs/promises';
import { join } from 'node:path';
import type { InstalledPackageInfo } from '../packages/registry.js';
import type { PackageUIAssets, ResourceScope } from './types.js';

/** 当前生效的宿主段名（M3 落地时加 'web'——多宿主逐段发现）。 */
const HOST_SEGMENTS = ['tui'] as const;

/** 渲染器类型目录（镜像后端资源类型）：frontend/<host>/<type>/<name>.ts。 */
const RENDERER_TYPE_DIRS = ['tools', 'user_tools'] as const;

/** 收集某文件约定目录下的条目（纯发现域——只收顶层 .ts，跳过测试）。 */
async function collectConventionDir(
  dir: string,
  into: Map<string, string>,
): Promise<void> {
  if (!existsSync(dir)) return;
  let files: string[] = [];
  try {
    files = await readdir(dir);
  } catch {
    return;
  }
  for (const file of files) {
    if (!file.endsWith('.ts') || file.endsWith('.test.ts')) continue;
    const name = file.slice(0, -'.ts'.length);
    // 同名碰撞：tools/ 优先（先扫 user_tools 后扫 tools，后者覆盖前者）
    into.set(name, join(dir, file));
  }
}

/** 探测一个已安装包的前端半区资产（无前端内容返回 null）。
 *
 * 半区定位：A 型复合包在 ``<根>/frontend/``；B 型纯前端包的**包根即前端半区**
 * （``tui/`` 直接在根下）。两类统一为"半区根 + 宿主段"扫描。
 */
export async function discoverUIAssets(
  pkg: InstalledPackageInfo,
): Promise<PackageUIAssets | null> {
  const frontendDir = join(pkg.installPath, 'frontend');
  const halfRoot = existsSync(frontendDir) ? frontendDir : pkg.installPath;

  // 主题资产（<半区>/themes/*.json——宿主无关纯数据）
  const themes = new Map<string, string>();
  const themesDir = join(halfRoot, 'themes');
  if (existsSync(themesDir)) {
    let files: string[] = [];
    try {
      files = await readdir(themesDir);
    } catch {
      files = [];
    }
    for (const file of files) {
      if (file.endsWith('.json')) themes.set(file.slice(0, -'.json'.length), join(themesDir, file));
    }
  }

  // 宿主段：取第一个存在的宿主（当前仅 tui——M3 多宿主时按宿主身份选取）
  let renderers = new Map<string, string>();
  let extensionEntry: string | undefined;
  for (const host of HOST_SEGMENTS) {
    const hostDir = join(halfRoot, host);
    if (!existsSync(hostDir)) continue;
    // 渲染器：user_tools 先扫、tools 后扫（tools 同名覆盖优先）
    for (const typeDir of RENDERER_TYPE_DIRS) {
      await collectConventionDir(join(hostDir, typeDir), renderers);
    }
    const entryPath = join(hostDir, 'index.ts');
    if (existsSync(entryPath)) extensionEntry = entryPath;
    break; // 单宿主生效——找到即停
  }

  // 没有任何可加载内容，视为无前端资产
  if (renderers.size === 0 && extensionEntry === undefined && themes.size === 0) return null;

  // npm 依赖探测：package.json 在 frontend/ 半区（A 型）或包根（B 型——根即半区）
  const halfManifest = join(halfRoot, 'package.json');
  const needsNpmInstall =
    existsSync(halfManifest) && !existsSync(join(halfRoot, 'node_modules'));

  return {
    packageName: pkg.name,
    scope: pkg.scope,
    installPath: pkg.installPath,
    renderers,
    extensionEntry,
    themes,
    needsNpmInstall,
    // 自愈工作目录与判定同目录（A 型 frontend/ 半区，B 型包根）——
    // 安装器 _npm_manifest_dir 同规则
    ...(existsSync(halfManifest) ? { npmDir: halfRoot } : {}),
  };
}

/**
 * 探测一个散养资产根（``frontend/<host>/`` 直挂资产，无包身份）。
 *
 * *root* 即宿主段根（如 ``~/.nova/agent/frontend/tui``）；识别
 * ``tools/<tool>.ts`` 渲染器、``dialogs/<name>.ts`` 对话框与 ``index.ts``
 * 扩展入口。无任何可加载内容返回 null。
 *
 * trust 门控不在本层：project 级根的"未信任不 stat"由编排层
 * （``runtime.refreshPackages``）在调用前裁决；user 级永远可信。
 * 来源标签即 scope 值（``user`` / ``project``）——slot 注册 source 与
 * 碰撞诊断直接消费；覆盖优先级由编排层的注册顺序表达
 * （project 散养最后注册——project > user > package > builtin）。
 */
export async function discoverLooseUIAssets(
  root: string,
  scope: ResourceScope,
): Promise<PackageUIAssets | null> {
  if (!existsSync(root)) return null;

  const renderers = new Map<string, string>();
  await collectConventionDir(join(root, 'tools'), renderers);
  const dialogs = new Map<string, string>();
  await collectConventionDir(join(root, 'dialogs'), dialogs);
  const entryPath = join(root, 'index.ts');
  const extensionEntry = existsSync(entryPath) ? entryPath : undefined;

  if (renderers.size === 0 && dialogs.size === 0 && extensionEntry === undefined) return null;

  return {
    packageName: scope, // 散养无包名——scope 即来源标签
    scope,
    installPath: root,
    renderers,
    dialogs,
    extensionEntry,
    themes: new Map(), // 散养 themes 不走本管线（user 级归 theme.ts 目录约定）
    needsNpmInstall: false, // 配置目录不跑 npm 自愈
  };
}

/** 散养双根发现（runtime.refreshPackages 的编排原语——路径由调用方现取注入）。 */
export interface DiscoverLooseAssetsOptions {
  /** user 级散养根（永远可信，恒扫描）。 */
  userRoot: string;
  /** project 级散养根（``<cwd>/.nova/frontend/<host>``）。 */
  projectRoot: string;
  /** 项目信任决议（后端快照）：false 时 project 根**不 stat 不 import**。 */
  trusted: boolean;
}

/**
 * 散养双根发现：user 恒收在前、project 可信时收在后——返回顺序即注册
 * 顺序（覆盖优先级 project > user > package > builtin 由此表达）。
 */
export async function discoverLooseAssets(
  options: DiscoverLooseAssetsOptions,
): Promise<PackageUIAssets[]> {
  const loose: PackageUIAssets[] = [];
  const user = await discoverLooseUIAssets(options.userRoot, 'user');
  if (user !== null) loose.push(user);
  if (options.trusted) {
    const project = await discoverLooseUIAssets(options.projectRoot, 'project');
    if (project !== null) loose.push(project);
  }
  return loose;
}
