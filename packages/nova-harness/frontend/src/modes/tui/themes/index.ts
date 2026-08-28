/**
 * 主题出口：动态消费面 + 运行时 API。
 *
 * ``colors`` / ``markdownTheme`` / ``syntaxColors`` / ``editorTheme`` 为
 * **活引用**（Proxy / 函数体转发到当前主题）——组件 import 一次、
 * ``setTheme`` 切换后渲染自动取新色，消费面零改动。
 */

import type { EditorTheme, MarkdownTheme } from '@earendil-works/pi-tui';

import {
  getCurrentThemeFace,
  type ThemeColors,
} from './theme.js';

export const colors: ThemeColors = new Proxy({} as ThemeColors, {
  get: (_target, prop: string) =>
    getCurrentThemeFace().colors[prop as keyof ThemeColors],
});

export const markdownTheme: MarkdownTheme = new Proxy({} as MarkdownTheme, {
  get: (_target, prop: keyof MarkdownTheme) => getCurrentThemeFace().markdownTheme[prop],
});

export const syntaxColors: Record<string, string | number> = new Proxy(
  {} as Record<string, string | number>,
  { get: (_target, prop: string) => getCurrentThemeFace().syntaxColors[prop] },
);

/** 编辑器边框色钩子（app 装配：bash 模式/thinking 级别状态色——渲染帧现取）。 */
let borderColorHook: (() => (s: string) => string) | undefined;

export function setEditorBorderColorHook(hook: () => (s: string) => string): void {
  borderColorHook = hook;
}

/** thinking 级别边框色（当前主题，缺 token 回退 borderMuted）。 */
export function thinkingBorderColor(level: string): (s: string) => string {
  return getCurrentThemeFace().thinkingBorderColor(level);
}

export const editorTheme: EditorTheme = {
  borderColor: (s) =>
    (borderColorHook?.() ?? getCurrentThemeFace().editorTheme.borderColor)(s),
  selectList: {
    selectedPrefix: (s) => getCurrentThemeFace().editorTheme.selectList.selectedPrefix(s),
    selectedText: (s) => getCurrentThemeFace().editorTheme.selectList.selectedText(s),
    description: (s) => getCurrentThemeFace().editorTheme.selectList.description(s),
    scrollInfo: (s) => getCurrentThemeFace().editorTheme.selectList.scrollInfo(s),
    noMatch: (s) => getCurrentThemeFace().editorTheme.selectList.noMatch(s),
  },
};

export {
  bindTerminalThemeSync,
  detectTerminalTheme,
  getAvailableThemes,
  getCurrentThemeName,
  getExportThemeData,
  initTheme,
  onThemeChange,
  registerPackageThemePaths,
  registerPackageThemes,
  setCustomThemesDirForTest,
  setTheme,
  stopThemeWatch,
  watchThemeFiles,
  type ExportThemeData,
  type TerminalColorSchemeSource,
  type ThemeInfo,
} from './theme.js';
export { parseThemeJson, type ThemeJson } from './theme-json.js';
