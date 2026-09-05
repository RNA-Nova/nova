/**
 * 呈现资源加载器（resources/ 子系统，设计 v3 §6.1）。
 *
 * 加载 ui/ 资产到 slots：
 * - ``frontend/<host>/tools|user_tools/*.ts``：渲染器（文件名即工具名）；
 * - ``dialogs/<name>.ts``：文件约定对话框（散养根专属——默认导出
 *   DialogFactory，注册 ``dialog:<name>``；包的对话框走 index.ts 编程式注册）；
 * - ``frontend/<host>/index.ts``：全量扩展入口——默认导出工厂 ``(api) => void``，
 *   经 ExtensionUIAPI 编程式注册（渲染器/区域部件；同包内工厂后于
 *   renderers/dialogs 文件执行——编程式可覆盖文件约定，同源不算碰撞）。
 *
 * - 发现源：../discovery.ts（唯一——不散落第二处探测逻辑）；
 * - trust 过滤：../trust.ts（编排层——本加载器只收已获准加载的包）；
 * - 加载：jiti（用户写裸 .ts 免预编译）；
 *   别名 ``nova-tui`` → 本包自身，渲染器可写
 *   ``import { detailsOf } from 'nova-tui/presentation/blocks.js'``；
 * - npm 自愈：node_modules 缺失先补跑 install，失败仍尝试加载
 *   （渲染器未必需要依赖）；
 * - 注册统一走 ExtensionUIAPI 通道（registerRenderer/registerRegion 与
 *   文件约定同源同路）——覆盖碰撞经 onCollision 收集为诊断
 *   （slots 是纯注册表）；
 * - 诊断即数据：失败/碰撞统一进 result.diagnostics（不阻断、不日志）。
 */

// jiti/static：静态引入 babel transform——bun --compile 才能把它打进二进制
// （pi core/extensions/loader.ts 同款；普通 node/tsx 模式同样可用）
import { createJiti } from 'jiti/static';
import { stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { isBunBinary } from '../binary.js';
// —— 编译二进制（bun --compile）模式的宿主模块锚点 ——
// 必须静态 import，bun 才会把这些模块打进二进制；运行时经 jiti
// virtualModules 直供包渲染器（磁盘上没有宿主 dist 可供别名解析）。
import * as bundledPiTui from '@earendil-works/pi-tui';
import * as bundledNovaTui from '../index.js';
import * as bundledBlocksDiff from '../modes/tui/blocks/diff.js';
import * as bundledBlocksTable from '../modes/tui/blocks/table.js';
import * as bundledDynamicBorder from '../modes/tui/components/layout/dynamic-border.js';
import * as bundledSearchable from '../modes/tui/components/pickers/searchable.js';
import * as bundledThemes from '../modes/tui/themes/index.js';
import * as bundledTuiSettings from '../modes/tui/utils/tui-settings.js';
import { healNpmDependencies } from '../packages/npm.js';
import {
  createExtensionUIAPI,
  type DialogFactory,
  type ExtensionUIContext,
  type ExtensionUIAPI,
} from '../presentation/extension-api.js';
import type { UISettings, UIStateStore } from '../settings/store.js';
import type { NovaRenderer, PreviewComputer } from '../presentation/blocks.js';
import type { SlotRegistry } from '../presentation/slots.js';
import type {
  PackageUIAssets,
  ResourceDiagnostic,
  ResourceLoadResult,
} from './types.js';
import { parseThemeJson, resolveThemeColors, type ThemeJson } from '../presentation/theme-json.js';
import { readFileSync } from 'node:fs';

/** 本包源码/产物根（jiti 别名目标——renderers 的 import 契约锚点）。
 *  惰性：编译二进制中 bunfs 路径无磁盘对应，只在别名分支才求值。 */
const runtimeRoot = () => fileURLToPath(new URL('..', import.meta.url));

/** pi-tui 宿主副本入口（jiti 别名目标——包组件与宿主共享同一模块实例）。
 *  惰性：编译二进制中 createRequire 无法从 bunfs 解析（顶层求值会让 --help 都崩）。 */
const piTuiEntry = () => createRequire(import.meta.url).resolve('@earendil-works/pi-tui');

/** 编译二进制模式下经 jiti virtualModules 直供的宿主模块表（精确 specifier 匹配）。
 *  覆盖官方 bundles 渲染器用到的全部宿主导入面；第三方包用表外子路径会加载失败
 *  （生产化需收敛渲染器导入契约或构建期生成此表）。 */
const VIRTUAL_MODULES: Record<string, unknown> = {
  'nova-tui': bundledNovaTui,
  'nova-tui/modes/tui/blocks/diff': bundledBlocksDiff,
  'nova-tui/modes/tui/blocks/table': bundledBlocksTable,
  'nova-tui/modes/tui/components/layout/dynamic-border': bundledDynamicBorder,
  'nova-tui/modes/tui/components/pickers/searchable': bundledSearchable,
  'nova-tui/modes/tui/themes/index': bundledThemes,
  'nova-tui/modes/tui/utils/tui-settings': bundledTuiSettings,
  '@earendil-works/pi-tui': bundledPiTui,
};

/**
 * 模块产物缓存：按 文件路径 + mtime
 * 自失效——refreshPackages 重复刷新时未变化的文件零 jiti 编译开销；
 * 文件内容变化（mtime 变）自动重载，无需显式失效通道。
 * 注意 1：project trust 过滤在编排层（本加载器只收已获准加载的包），
 * 不被信任的包永不进入这里、也永不写入缓存。
 * 注意 2：index.ts 缓存的是**工厂函数**——每次刷新都重新执行工厂
 * （slots 整体替换后必须重新注册），缓存只省 jiti 编译。
 * 注意 3：jiti 自带的 fsCache/moduleCache 跨实例共享且不校验 mtime——
 * 已禁用，本层为唯一缓存（双层缓存语义失真，实测见 P0 记录）。
 */
interface CachedModule {
  mtimeMs: number;
  render?: NovaRenderer;
  preview?: PreviewComputer;
  factory?: ExtensionUIEntryFactory;
  /** 文件约定对话框工厂（散养根 dialogs/<name>.ts）。 */
  dialog?: DialogFactory;
}
const moduleCache = new Map<string, CachedModule>();

/** <host>/index.ts 的默认导出形态（扩展入口工厂）。 */
export type ExtensionUIEntryFactory = (api: ExtensionUIAPI) => void;

export interface ResourceLoaderOptions {
  /** 绕过结果缓存（reload 场景：开发者改了渲染器需立即生效）。 */
  force?: boolean;
  /** 命令/快捷键执行上下文（runtime 注入——扩展 registerCommand/Shortcut 的 ctx）。 */
  uiContext?: ExtensionUIContext;
  /** 扩展设置/内部 KV 存储（runtime 持有的子系统实例——api.settings/state 的后端）。 */
  uiSettings?: UISettings;
  uiState?: UIStateStore;
  /** dialog:* 注册变化钩子（runtime 注入——触发能力重宣告）。 */
  onDialogChange?: () => void;
  /** npm 自愈完成钩子（runtime 注入——补装完刷新 + 通知；ok=是否成功）。 */
  onNpmHealed?: (packageName: string, ok: boolean) => void;
}

/** 加载包的 ui/ 资产到 slots（纯管线：调用方负责 trust 过滤与发现）。 */
export async function loadUIAssets(
  assets: PackageUIAssets[],
  slots: SlotRegistry,
  options: ResourceLoaderOptions = {},
): Promise<ResourceLoadResult> {
  const result: ResourceLoadResult = { loaded: [], diagnostics: [], themes: new Map() };
  /** 主题来源表（碰撞诊断——result.themes 只存产物）。 */
  const themeSources = new Map<string, string>();

  const jiti = createJiti(import.meta.url, {
    moduleCache: false,
    fsCache: false,
    // 编译二进制：宿主模块不在磁盘——virtualModules 直供内存模块对象；
    // 否则走 dist 别名锚定宿主副本（双包分裂会破坏 getKeybindings 等模块级单例）
    ...(isBunBinary
      ? { virtualModules: VIRTUAL_MODULES, tryNative: false }
      : {
          alias: {
            'nova-tui': runtimeRoot(),
            '@earendil-works/pi-tui': piTuiEntry(),
          },
        }),
  });

  /** 缓存读取（mtime 未变命中）；force 旁路。 */
  const readCache = async (filePath: string): Promise<CachedModule | undefined> => {
    if (options.force) return undefined;
    const cached = moduleCache.get(filePath);
    if (cached === undefined) return undefined;
    const mtimeMs = (await stat(filePath)).mtimeMs;
    return mtimeMs === cached.mtimeMs ? cached : undefined;
  };

  const writeCache = async (filePath: string, entry: Omit<CachedModule, 'mtimeMs'>) => {
    moduleCache.set(filePath, { mtimeMs: (await stat(filePath)).mtimeMs, ...entry });
  };

  for (const pkg of assets) {
    if (pkg.needsNpmInstall) {
      // 不阻塞加载路径：后台补装（时长归 npm 自己的超时/重试管），
      // 完成经 onNpmHealed 回调由 runtime 刷新 + 通知；本轮该包缺依赖的
      // 渲染器 import 失败按诊断降级，补装后下轮上线
      result.diagnostics.push({
        type: 'warning',
        message: `包 ${pkg.packageName} 缺 npm 依赖——后台补装中，完成后自动生效`,
      });
      void healNpmDependencies(pkg.npmDir ?? pkg.installPath).then((ok) => {
        options.onNpmHealed?.(pkg.packageName, ok);
      });
    }

    // 包的注册通道（渲染器/区域部件/命令/快捷键同源同路；碰撞收集为诊断）
    const api = createExtensionUIAPI({
      slots,
      source: pkg.packageName,
      uiContext: options.uiContext,
      uiSettings: options.uiSettings,
      uiState: options.uiState,
      onDialogChange: options.onDialogChange,
      onCollision: (key, winner, loser) => {
        const separator = key.indexOf(':');
        const kind = key.slice(0, separator);
        const name = key.slice(separator + 1);
        result.diagnostics.push({
          type: 'collision',
          message: `UI 贡献覆盖：${key} 由 ${loser} → ${winner}`,
          collision: {
            kind: kind === 'tool' ? 'renderer' : kind,
            name,
            winner,
            loser,
          },
        });
      },
    });

    // —— ① tools|user_tools/*.ts（文件约定通道）——
    for (const [toolName, filePath] of pkg.renderers) {
      const startedAt = performance.now();
      try {
        const cached = await readCache(filePath);
        if (cached?.render !== undefined) {
          api.registerRenderer(toolName, cached.render, cached.preview);
          result.loaded.push({
            name: toolName,
            source: pkg.packageName,
            durationMs: performance.now() - startedAt,
          });
          continue;
        }

        const mod: unknown = await jiti.import(filePath);
        const candidate =
          typeof mod === 'object' && mod !== null && 'default' in mod
            ? (mod as { default: unknown }).default
            : mod;
        if (typeof candidate !== 'function') {
          throw new Error('默认导出不是渲染函数');
        }
        const render = candidate as NovaRenderer;
        // 可选命名导出 preview：执行前只读预览计算器（edit diff 预览等）
        const previewCandidate =
          typeof mod === 'object' && mod !== null && 'preview' in mod
            ? (mod as { preview: unknown }).preview
            : undefined;
        const preview =
          typeof previewCandidate === 'function'
            ? (previewCandidate as PreviewComputer)
            : undefined;
        api.registerRenderer(toolName, render, preview);
        await writeCache(filePath, { render, preview });
        result.loaded.push({
          name: toolName,
          source: pkg.packageName,
          durationMs: performance.now() - startedAt,
        });
      } catch (error) {
        result.diagnostics.push({
          type: 'error',
          message: `渲染器加载失败（${pkg.packageName}/${toolName}）：${
            error instanceof Error ? error.message : String(error)
          }`,
          path: filePath,
        });
      }
    }

    // —— ② ui/themes/*.json（纯数据资产：校验 + 收集，宿主注册）——
    for (const [themeName, themePath] of pkg.themes) {
      try {
        const json = parseThemeJson(
          themePath,
          JSON.parse(readFileSync(themePath, 'utf-8')),
        );
        resolveThemeColors(json); // vars 引用提前解析（环/未定义在此暴露）
        // 同名后注册覆盖（包间主题名竞争——碰撞诊断在案）
        const existingSource = themeSources.get(themeName);
        if (existingSource !== undefined && existingSource !== pkg.packageName) {
          result.diagnostics.push({
            type: 'collision',
            message: `主题覆盖：${themeName} 由 ${existingSource} → ${pkg.packageName}`,
            path: themePath,
          });
        }
        themeSources.set(themeName, pkg.packageName);
        result.themes.set(themeName, json);
        result.loaded.push({
          name: `theme:${themeName}`,
          source: pkg.packageName,
          durationMs: 0,
        });
      } catch (error) {
        result.diagnostics.push({
          type: 'error',
          message: `主题加载失败（${pkg.packageName}/${themeName}）：${
            error instanceof Error ? error.message : String(error)
          }`,
          path: themePath,
        });
      }
    }

    // —— ③ dialogs/*.ts（文件约定对话框——散养根专属通道）——
    for (const [dialogName, filePath] of pkg.dialogs ?? []) {
      const startedAt = performance.now();
      try {
        let factory = (await readCache(filePath))?.dialog;
        if (factory === undefined) {
          const mod: unknown = await jiti.import(filePath);
          const candidate =
            typeof mod === 'object' && mod !== null && 'default' in mod
              ? (mod as { default: unknown }).default
              : mod;
          if (typeof candidate !== 'function') {
            throw new Error('默认导出不是对话框工厂函数');
          }
          factory = candidate as DialogFactory;
          await writeCache(filePath, { dialog: factory });
        }
        // 注册即触发能力重宣告（system/capabilities——后端 has_capability 放行）
        api.registerDialog?.(dialogName, factory);
        result.loaded.push({
          name: `dialog:${dialogName}`,
          source: pkg.packageName,
          durationMs: performance.now() - startedAt,
        });
      } catch (error) {
        result.diagnostics.push({
          type: 'error',
          message: `对话框加载失败（${pkg.packageName}/${dialogName}）：${
            error instanceof Error ? error.message : String(error)
          }`,
          path: filePath,
        });
      }
    }

    // —— ④ <host>/index.ts（编程式扩展入口，后执行可覆盖文件约定）——
    if (pkg.extensionEntry !== undefined) {
      const filePath = pkg.extensionEntry;
      const startedAt = performance.now();
      try {
        let factory = (await readCache(filePath))?.factory;
        if (factory === undefined) {
          const mod: unknown = await jiti.import(filePath);
          const candidate =
            typeof mod === 'object' && mod !== null && 'default' in mod
              ? (mod as { default: unknown }).default
              : mod;
          if (typeof candidate !== 'function') {
            throw new Error('index.ts 默认导出不是工厂函数');
          }
          factory = candidate as ExtensionUIEntryFactory;
          await writeCache(filePath, { factory });
        }
        factory(api); // 每次刷新都执行（slots 整体替换后必须重新注册）
        result.loaded.push({
          name: 'tui/index',
          source: pkg.packageName,
          durationMs: performance.now() - startedAt,
        });
      } catch (error) {
        result.diagnostics.push({
          type: 'error',
          message: `扩展入口加载失败（${pkg.packageName}/tui/index.ts）：${
            error instanceof Error ? error.message : String(error)
          }`,
          path: filePath,
        });
      }
    }
  }

  return result;
}
