/**
 * 主题运行时：ThemeFace 生成 + 全局当前主题单例。
 *
 * - ``createThemeFace``：ThemeJson → 消费面四件套（colors chalk 函数集 /
 *   markdownTheme / syntaxColors / editorTheme）——色值三形态（hex / "" /
 *   256 索引）分别经 chalk.hex / identity / chalk.ansi256 承载；
 * - 单例：``initTheme``（启动，COLORFGBG 终端背景检测兜底 dark）/
 *   ``setTheme``（切换，失败回退 dark 并返回 error——pi 同款语义）/
 *   ``onThemeChange``（切换回调——全量重渲由装配根接）；
 * - 主题来源：内建 dark/light + 自定义目录 ``frontend/tui/themes/*.json``
 *   （内建优先，坏文件诊断跳过——pi getCustomThemeInfos 对位）。
 * - ``automatic`` 档：逻辑主题名（可持久化）——按终端亮暗在内建
 *   dark/light 间取值；初始取 COLORFGBG 检测，``bindTerminalThemeSync``
 *   绑定 pi-tui 配色通知后跟随终端实时切换（pi autoSync 对位）；
 * - ``watchThemeFiles``：用户主题目录 + 已登记包主题文件的 fs.watch
 *   热更新（命中当前主题即重读重建，onThemeChange 触发全量重渲）。
 *
 * 终端检测只做 COLORFGBG 环境变量路径；OSC 11 终端查询挂账（需要时补）。
 */

import { existsSync, readdirSync, readFileSync, watch, type FSWatcher } from 'node:fs';
import { dirname, join } from 'node:path';

import chalk from 'chalk';
import type { EditorTheme, MarkdownTheme } from '@earendil-works/pi-tui';

import { userFrontendDir } from '../../../paths.js';
import { BUILTIN_DARK } from './builtin-dark.js';
import { BUILTIN_LIGHT } from './builtin-light.js';
import {
  BG_TOKENS,
  REQUIRED_COLOR_TOKENS,
  parseThemeJson,
  resolveThemeColors,
  type ThemeJson,
} from './theme-json.js';

type ColorFn = (s: string) => string;

/** colors 消费面（26 个语义色函数）。 */
export type ThemeColors = Record<(typeof REQUIRED_COLOR_TOKENS)[number], ColorFn>;

export interface ThemeFace {
  name: string;
  colors: ThemeColors;
  markdownTheme: MarkdownTheme;
  syntaxColors: Record<string, string | number>;
  editorTheme: EditorTheme;
  /** thinking 级别边框色（可选 token——主题缺失时回退 borderMuted）。 */
  thinkingBorderColor: (level: string) => ColorFn;
}

// ---------------------------------------------------------------------------
// ThemeFace 生成
// ---------------------------------------------------------------------------

function colorFn(value: string | number, bg: boolean): ColorFn {
  if (value === '') return (s) => s; // 终端默认色
  if (typeof value === 'number') {
    return bg ? (s) => chalk.bgAnsi256(value)(s) : (s) => chalk.ansi256(value)(s);
  }
  return bg ? (s) => chalk.bgHex(value)(s) : (s) => chalk.hex(value)(s);
}

/** ThemeJson → 消费面四件套。 */
export function createThemeFace(json: ThemeJson): ThemeFace {
  const resolved = resolveThemeColors(json);
  const fn = (token: string): ColorFn => colorFn(resolved[token] ?? '', BG_TOKENS.has(token));

  const colors = {} as Record<string, ColorFn>;
  for (const token of REQUIRED_COLOR_TOKENS) colors[token] = fn(token);

  const markdownTheme: MarkdownTheme = {
    heading: fn('mdHeading'),
    link: fn('mdLink'),
    linkUrl: fn('mdLinkUrl'),
    code: fn('mdCode'),
    codeBlock: fn('mdCodeBlock'),
    codeBlockBorder: fn('mdCodeBlockBorder'),
    quote: fn('mdQuote'),
    quoteBorder: fn('mdQuoteBorder'),
    hr: fn('mdHr'),
    listBullet: fn('mdListBullet'),
    bold: (s) => chalk.bold(s),
    italic: (s) => chalk.italic(s),
    strikethrough: (s) => chalk.strikethrough(s),
    underline: (s) => chalk.underline(s),
  };

  const syntaxColors: Record<string, string | number> = {};
  for (const token of REQUIRED_COLOR_TOKENS) {
    if (token.startsWith('syntax')) syntaxColors[token] = resolved[token] ?? '';
  }

  const editorTheme: EditorTheme = {
    borderColor: fn('borderMuted'),
    selectList: {
      selectedPrefix: fn('accent'),
      selectedText: fn('accent'),
      description: fn('muted'),
      scrollInfo: fn('dim'),
      noMatch: fn('warning'),
    },
  };

  // thinking 级别 → 可选 token（pi 命名直搬；max 缺省回退 xhigh——pi 同款）
  const thinkingToken = (level: string): string =>
    `thinking${level.charAt(0).toUpperCase()}${level.slice(1)}`;
  const thinkingBorderColor = (level: string): ColorFn => {
    const token = thinkingToken(level);
    const value = resolved[token] ?? (level === 'max' ? resolved.thinkingXhigh : undefined);
    return value !== undefined ? colorFn(value, false) : fn('borderMuted');
  };

  return {
    name: json.name,
    colors: colors as ThemeColors,
    markdownTheme,
    syntaxColors,
    editorTheme,
    thinkingBorderColor,
  };
}

// ---------------------------------------------------------------------------
// 主题发现（内建 + 自定义目录）
// ---------------------------------------------------------------------------

export interface ThemeInfo {
  name: string;
  /** 来源（"builtin" 或文件路径——选择器描述列显示）。 */
  source: string;
}

/** 自定义主题目录（诊断用途可注入覆盖——测试隔离）。 */
let customThemesDirOverride: string | undefined;

export function setCustomThemesDirForTest(dir: string | undefined): void {
  customThemesDirOverride = dir;
}

function customThemesDir(): string {
  return customThemesDirOverride ?? join(userFrontendDir(), 'themes');
}

const BUILTIN_THEMES: Record<string, ThemeJson> = {
  dark: BUILTIN_DARK,
  light: BUILTIN_LIGHT,
};

/** 包注册主题（ui/themes/*.json——loader 全量替换，与 slots 整体替换语义配套）。 */
let packageThemes = new Map<string, ThemeJson>();

/** 包主题全量替换（loader 在 refreshPackages 时调用——卸载包的主题随之消失）。 */
export function registerPackageThemes(themes: Map<string, ThemeJson>): void {
  packageThemes = themes;
}

/** 可用主题清单（builtin > 用户目录 > 包——pi 优先级对位；坏自定义文件进 diagnostics）。 */
export function getAvailableThemes(): { themes: ThemeInfo[]; diagnostics: string[] } {
  const diagnostics: string[] = [];
  const seen = new Set<string>();
  const themes: ThemeInfo[] = [];
  const add = (name: string, source: string) => {
    if (seen.has(name)) return;
    seen.add(name);
    themes.push({ name, source });
  };

  for (const name of Object.keys(BUILTIN_THEMES)) add(name, 'builtin');

  const dir = customThemesDir();
  if (existsSync(dir)) {
    for (const file of readdirSync(dir).sort()) {
      if (!file.endsWith('.json')) continue;
      const path = join(dir, file);
      try {
        const json = parseThemeJson(path, JSON.parse(readFileSync(path, 'utf-8')));
        resolveThemeColors(json); // vars 引用提前解析（环/未定义在此暴露）
        add(json.name, path);
      } catch (error) {
        diagnostics.push(`${path}: ${(error as Error).message}`);
      }
    }
  }
  for (const name of packageThemes.keys()) add(name, 'package');
  return { themes, diagnostics };
}

function loadThemeJson(name: string): ThemeJson {
  if (name in BUILTIN_THEMES) return BUILTIN_THEMES[name]!;
  const path = join(customThemesDir(), `${name}.json`);
  if (existsSync(path)) {
    const json = parseThemeJson(path, JSON.parse(readFileSync(path, 'utf-8')));
    resolveThemeColors(json);
    return json;
  }
  const fromPackage = packageThemes.get(name);
  if (fromPackage !== undefined) return fromPackage;
  throw new Error(`主题不存在：${name}`);
}

// ---------------------------------------------------------------------------
// 全局当前主题
// ---------------------------------------------------------------------------

let currentFace: ThemeFace = createThemeFace(BUILTIN_DARK);
let currentName = 'dark';
/** 当前生效的 ThemeJson（automatic 档为解析出的内建——export 取色不重新查名）。 */
let currentJson: ThemeJson = BUILTIN_DARK;
/** automatic 模式当前解析的终端亮暗（配色通知去重——同值不重 apply）。 */
let automaticScheme: 'dark' | 'light' | undefined;
const changeCallbacks: Array<() => void> = [];

export function getCurrentThemeFace(): ThemeFace {
  return currentFace;
}

export function getCurrentThemeName(): string {
  return currentName;
}

export function onThemeChange(callback: () => void): void {
  changeCallbacks.push(callback);
}

function apply(name: string, json: ThemeJson): void {
  currentFace = createThemeFace(json);
  currentName = name;
  currentJson = json;
  for (const callback of changeCallbacks) callback();
}

// ---------------------------------------------------------------------------
// automatic 档（跟随终端亮暗——pi autoSync 对位）
// ---------------------------------------------------------------------------

/** 终端配色通知源（pi-tui TUI 的最小切面——测试可喂假对象）。 */
export interface TerminalColorSchemeSource {
  onTerminalColorSchemeChange(listener: (scheme: 'dark' | 'light') => void): () => void;
  setTerminalColorSchemeNotifications(enabled: boolean): void;
}

let schemeSource: TerminalColorSchemeSource | undefined;

/** automatic 应用：按终端亮暗取内建（逻辑名保持 'automatic'——持久化/显示语义无损）。 */
function applyAutomatic(scheme: 'dark' | 'light'): void {
  automaticScheme = scheme;
  apply('automatic', scheme === 'light' ? BUILTIN_LIGHT : BUILTIN_DARK);
}

/** 配色通知开关同步（仅 automatic 档开启——省电也避免无关回调）。 */
function syncSchemeNotifications(): void {
  schemeSource?.setTerminalColorSchemeNotifications(currentName === 'automatic');
}

/**
 * 绑定终端配色通知（automatic 档的跟随引擎；装配根启动调用一次）。
 * 终端配色变化且当前为 automatic → 重解析应用（onThemeChange 由 apply 发）。
 * 返回解绑函数。
 */
export function bindTerminalThemeSync(tui: TerminalColorSchemeSource): () => void {
  schemeSource = tui;
  syncSchemeNotifications();
  return tui.onTerminalColorSchemeChange((scheme) => {
    if (currentName !== 'automatic' || scheme === automaticScheme) return;
    applyAutomatic(scheme);
  });
}

/**
 * 切换主题；失败回退 dark 并返回 error（pi setTheme 同款语义——
 * 回退主题不再发回调：内容与切换前一致或已可用）。
 * 'automatic' 为逻辑档：按 COLORFGBG 检测取内建，永不失败。
 */
export function setTheme(name: string): { success: boolean; error?: string } {
  if (name === 'automatic') {
    applyAutomatic(detectTerminalTheme());
    syncSchemeNotifications();
    return { success: true };
  }
  try {
    automaticScheme = undefined;
    apply(name, loadThemeJson(name));
    syncSchemeNotifications();
    return { success: true };
  } catch (error) {
    automaticScheme = undefined;
    currentName = 'dark';
    currentJson = BUILTIN_DARK;
    currentFace = createThemeFace(BUILTIN_DARK);
    for (const callback of changeCallbacks) callback();
    syncSchemeNotifications();
    return { success: false, error: (error as Error).message };
  }
}

/** COLORFGBG 终端背景检测（pi detectTerminalBackgroundFromEnv 简化版）。 */
export function detectTerminalTheme(): 'dark' | 'light' {
  const colorfgbg = process.env.COLORFGBG ?? '';
  const parts = colorfgbg.split(';');
  const bg = Number.parseInt(parts[parts.length - 1]?.trim() ?? '', 10);
  // ANSI 背景索引 0-6/8 深色系判 dark，7/15 及亮色系判 light（经验阈值）
  if (Number.isInteger(bg) && bg >= 0 && bg <= 255) {
    return bg === 7 || bg === 15 || (bg >= 231 && bg <= 255 && bg % 2 === 0) ? 'light' : 'dark';
  }
  return 'dark';
}

/** 启动初始化：指定名 / 检测兜底；'automatic' 取检测档；失败回退 dark（静默，与 pi 一致）。 */
export function initTheme(name?: string): void {
  const target = name ?? detectTerminalTheme();
  try {
    if (target === 'automatic') {
      automaticScheme = detectTerminalTheme();
      currentFace = createThemeFace(automaticScheme === 'light' ? BUILTIN_LIGHT : BUILTIN_DARK);
      currentJson = automaticScheme === 'light' ? BUILTIN_LIGHT : BUILTIN_DARK;
    } else {
      automaticScheme = undefined;
      currentJson = loadThemeJson(target);
      currentFace = createThemeFace(currentJson);
    }
    currentName = target;
  } catch {
    automaticScheme = undefined;
    currentFace = createThemeFace(BUILTIN_DARK);
    currentJson = BUILTIN_DARK;
    currentName = 'dark';
  }
  syncSchemeNotifications();
}

// ---------------------------------------------------------------------------
// 主题文件 watcher（热更新——fs.watch，零新依赖）
// ---------------------------------------------------------------------------

/** 包主题文件（主题名 → 绝对路径）——装配侧登记（loader 的 name→path 直喂）。 */
let packageThemePaths = new Map<string, string>();

let themeWatchers: FSWatcher[] = [];
let watchReloadTimer: ReturnType<typeof setTimeout> | undefined;

/** 登记包主题文件路径（随 registerPackageThemes 全量替换语义同步换人）。 */
export function registerPackageThemePaths(paths: ReadonlyMap<string, string>): void {
  packageThemePaths = new Map(paths);
}

/**
 * 监听主题文件变化（用户主题目录 + 已登记包主题文件所在目录），
 * 变化去抖 100ms 后命中当前主题即重载：重读磁盘 + 重建 face +
 * onThemeChange 触发全量重渲；坏文件按 setTheme 语义回退 dark。
 * 返回停止函数（watcher 非 persistent——不拖住进程退出）。
 */
export function watchThemeFiles(): () => void {
  stopThemeWatch();
  const dirs = new Set<string>();
  if (existsSync(customThemesDir())) dirs.add(customThemesDir());
  for (const path of packageThemePaths.values()) dirs.add(dirname(path));
  for (const dir of dirs) {
    try {
      themeWatchers.push(watch(dir, { persistent: false }, () => scheduleThemeReload()));
    } catch {
      // 目录不可监听（权限/已删）——跳过不阻断
    }
  }
  return stopThemeWatch;
}

/** 停止监听（测试还原与重复调用前的清理）。 */
export function stopThemeWatch(): void {
  for (const watcher of themeWatchers) watcher.close();
  themeWatchers = [];
  if (watchReloadTimer !== undefined) {
    clearTimeout(watchReloadTimer);
    watchReloadTimer = undefined;
  }
}

/** 去抖重载：编辑器保存常触发一串事件；原子写（tmp+rename）也产多帧。 */
function scheduleThemeReload(): void {
  if (watchReloadTimer !== undefined) clearTimeout(watchReloadTimer);
  watchReloadTimer = setTimeout(() => {
    watchReloadTimer = undefined;
    reloadCurrentTheme();
  }, 100);
  watchReloadTimer.unref();
}

/** 当前主题热重载（内建/automatic 无文件源——跳过）。 */
function reloadCurrentTheme(): void {
  if (currentName === 'automatic' || currentName in BUILTIN_THEMES) return;
  // 包主题：先把文件重读进注册表（loadThemeJson 走内存 Map——不刷拿到旧数据）；
  // 重读失败（保存中途的撕裂读）保留旧数据，本次不重载
  const packagePath = packageThemePaths.get(currentName);
  if (packagePath !== undefined && existsSync(packagePath)) {
    try {
      const json = parseThemeJson(packagePath, JSON.parse(readFileSync(packagePath, 'utf-8')));
      resolveThemeColors(json);
      packageThemes.set(currentName, json);
    } catch {
      return;
    }
  }
  setTheme(currentName); // 重读 + 重建 + 回调；失败回退 dark（pi setTheme 语义）
}

// ---------------------------------------------------------------------------
// HTML 导出主题数据（export/ 子系统经此取色——256 索引转 hex，export 段
// 解析或从 userMessageBg 派生，pi getResolvedThemeColors/deriveExportColors 对位）
// ---------------------------------------------------------------------------

/** ANSI 256 索引 → hex（pi ansi256ToHex 直搬）。 */
export function ansi256ToHex(index: number): string {
  const basicColors = [
    '#000000', '#800000', '#008000', '#808000', '#000080', '#800080', '#008080', '#c0c0c0',
    '#808080', '#ff0000', '#00ff00', '#ffff00', '#0000ff', '#ff00ff', '#00ffff', '#ffffff',
  ];
  if (index < 16) return basicColors[index]!;
  if (index < 232) {
    const cubeIndex = index - 16;
    const toHex = (n: number) => (n === 0 ? 0 : 55 + n * 40).toString(16).padStart(2, '0');
    return `#${toHex(Math.floor(cubeIndex / 36))}${toHex(Math.floor((cubeIndex % 36) / 6))}${toHex(cubeIndex % 6)}`;
  }
  const gray = (8 + (index - 232) * 10).toString(16).padStart(2, '0');
  return `#${gray}${gray}${gray}`;
}

function adjustBrightness(hex: string, factor: number): string {
  const cleaned = hex.replace('#', '');
  const r = Math.min(255, Math.round(parseInt(cleaned.slice(0, 2), 16) * factor));
  const g = Math.min(255, Math.round(parseInt(cleaned.slice(2, 4), 16) * factor));
  const b = Math.min(255, Math.round(parseInt(cleaned.slice(4, 6), 16) * factor));
  return `rgb(${r}, ${g}, ${b})`;
}

export interface ExportThemeData {
  /** 全量色键 → CSS 兼容值（CSS 自定义属性用）。 */
  cssColors: Record<string, string>;
  pageBg: string;
  cardBg: string;
  infoBg: string;
}

/** 当前主题的导出数据（256 索引转 hex、空串给默认文本色、export 段解析/派生）。 */
export function getExportThemeData(): ExportThemeData {
  // 取 currentJson（automatic 档为解析出的内建——不按名重查，避免逻辑名解析不到）
  const json = currentJson;
  const resolved = resolveThemeColors(json);
  const isLight = json.name === 'light';
  const defaultText = isLight ? '#000000' : '#e5e5e7';

  const cssColors: Record<string, string> = {};
  for (const [key, value] of Object.entries(resolved)) {
    if (typeof value === 'number') cssColors[key] = ansi256ToHex(value);
    else if (value === '') cssColors[key] = defaultText;
    else cssColors[key] = value;
  }

  // export 段：值经 vars 解析（可引用色板变量），缺省从 userMessageBg 派生
  const exportSection = json.export ?? {};
  const resolveExport = (value: string | number | undefined): string | undefined => {
    if (value === undefined) return undefined;
    const resolvedValue =
      typeof value === 'string' && !value.startsWith('#') && value !== ''
        ? resolved[value]
        : value;
    if (resolvedValue === undefined) return undefined;
    if (typeof resolvedValue === 'number') return ansi256ToHex(resolvedValue);
    return resolvedValue === '' ? defaultText : resolvedValue;
  };

  const userMessageBg = cssColors.userMessageBg ?? '#343541';
  const derived = {
    pageBg: adjustBrightness(userMessageBg, 0.7),
    cardBg: adjustBrightness(userMessageBg, 0.85),
    infoBg: (() => {
      const cleaned = userMessageBg.replace('#', '');
      const r = Math.min(255, parseInt(cleaned.slice(0, 2), 16) + 20);
      const g = Math.min(255, parseInt(cleaned.slice(2, 4), 16) + 15);
      const b = parseInt(cleaned.slice(4, 6), 16);
      return `rgb(${r}, ${g}, ${b})`;
    })(),
  };
  return {
    cssColors,
    pageBg: resolveExport(exportSection.pageBg) ?? derived.pageBg,
    cardBg: resolveExport(exportSection.cardBg) ?? derived.cardBg,
    infoBg: resolveExport(exportSection.infoBg) ?? derived.infoBg,
  };
}
