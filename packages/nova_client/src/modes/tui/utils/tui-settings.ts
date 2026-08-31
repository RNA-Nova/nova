/**
 * TUI 前端设置键（Node 层前端域 ``frontend/tui/settings.json`` 持久化）。
 *
 * 边界：纯前端关心的设置（树过滤器/编辑器内边距/终端集成开关……）不进后端
 * settings.json（schema 未知键拒绝，且这是前端域——见 settings/store.ts）。
 * 本模块是这些键的唯一注册点与类型化读取口：
 * - ``initTuiSettings``：绑定 UISettings 实例并声明键（装配根启动时调用一次，
 *   设置面板构造时兜底重入——同 owner 幂等）；
 * - getter：缺省回退默认值（未绑定/未设置均安全）；
 * - ``setTuiSetting``：设置面板的统一写入口（未绑定返回 false）。
 */

import type { UISettings } from 'nova-client';

/** /tree 选择器的过滤器模式（初始 filter——选择器内临时切换不回写）。 */
export type TreeFilterMode = 'default' | 'no-tools' | 'user-only' | 'labeled-only' | 'all';

export const TREE_FILTER_MODES: readonly TreeFilterMode[] = [
  'default',
  'no-tools',
  'user-only',
  'labeled-only',
  'all',
];

/** 键声明属主（冲突诊断用——TUI 内建域）。 */
const OWNER = 'nova-tui';

let store: UISettings | undefined;

/** 绑定存储 + 声明全部前端键（重复调用幂等——同 owner 重载）。 */
export function initTuiSettings(uiSettings: UISettings): void {
  store = uiSettings;
  uiSettings.define('tree_filter_mode', { type: 'string', default: 'default' }, OWNER);
  uiSettings.define('branch_summary_skip_prompt', { type: 'boolean', default: false }, OWNER);
  uiSettings.define('editor_padding', { type: 'number', default: 1 }, OWNER);
  uiSettings.define('autocomplete_max_items', { type: 'number', default: 5 }, OWNER);
  uiSettings.define('clear_on_shrink', { type: 'boolean', default: true }, OWNER);
  uiSettings.define('terminal_progress', { type: 'boolean', default: false }, OWNER);
  uiSettings.define('desktop_notify', { type: 'boolean', default: true }, OWNER);
}

/** 设置面板写入口（类型校验归 UISettings.set——未声明/类型错返回 false）。 */
export function setTuiSetting(key: string, value: unknown): boolean {
  return store?.set(key, value) ?? false;
}

export function getTreeFilterMode(): TreeFilterMode {
  const value = store?.get<string>('tree_filter_mode');
  return TREE_FILTER_MODES.includes(value as TreeFilterMode)
    ? (value as TreeFilterMode)
    : 'default';
}

/** 分支摘要（navigateTree）跳过确认提示——true 时直接执行不等确认。 */
export function isBranchSummarySkipPrompt(): boolean {
  return store?.get<boolean>('branch_summary_skip_prompt') ?? false;
}

/** 编辑器水平内边距（0-3，pi editorPaddingX 对位；默认 1——与装配根现状一致）。 */
export function getEditorPadding(): number {
  return clampInt(store?.get<number>('editor_padding'), 0, 3, 1);
}

/** 编辑器补全下拉可见条数（pi autocompleteMaxVisible 对位；默认 5）。 */
export function getAutocompleteMaxItems(): number {
  return clampInt(store?.get<number>('autocomplete_max_items'), 3, 20, 5);
}

/** 内容收缩时清空残余行（pi-tui TUI.setClearOnShrink；默认 true 与 pi-tui 一致）。 */
export function isClearOnShrink(): boolean {
  return store?.get<boolean>('clear_on_shrink') ?? true;
}

/** OSC 9;4 终端进度发射开关（pi showTerminalProgress 对位；默认关——同 pi）。 */
export function isTerminalProgressEnabled(): boolean {
  return store?.get<boolean>('terminal_progress') ?? false;
}

/** agent run 结束的桌面通知开关（OSC 9/777/99 三序列并发；默认开）。 */
export function isDesktopNotifyEnabled(): boolean {
  return store?.get<boolean>('desktop_notify') ?? true;
}

function clampInt(value: number | undefined, min: number, max: number, fallback: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback;
  return Math.max(min, Math.min(max, Math.floor(value)));
}
