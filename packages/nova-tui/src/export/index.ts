/**
 * 会话导出 HTML（**Node 层**——呈现归本层，零后端改动）。
 *
 * 形态：自包含单文件 HTML（template.html/css/js + vendored
 * marked/highlight.js，客户端渲染；MIT 许可）。本文件只做**模板装配**：
 * 主题变量注入 + base64 数据内联。数据零映射——线上条目（camelCase 契约）
 * 与 template.js 的消费形状天然一致。
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

/** 主题导出数据（调用方注入——TUI 经 getExportThemeData；保持本层宿主无关）。 */
export interface ExportThemeInput {
  cssColors: Record<string, string>;
  pageBg: string;
  cardBg: string;
  infoBg: string;
}

// ---------------------------------------------------------------------------
// 模板装配
// ---------------------------------------------------------------------------

const exportDir = dirname(fileURLToPath(import.meta.url));

function readAsset(name: string): string {
  return readFileSync(join(exportDir, name), 'utf-8');
}

export interface AssembleOptions {
  /** 会话数据（映射后的 entries + 头部信息）。 */
  sessionData: Record<string, unknown>;
  theme: ExportThemeInput;
}

/** 装配自包含 HTML（占位符替换 + base64 数据内联）。 */
export function assembleHtml(options: AssembleOptions): string {
  const { sessionData, theme } = options;
  const template = readAsset('template.html');
  const templateCss = readAsset('template.css');
  const templateJs = readAsset('template.js');
  const markedJs = readAsset(join('vendor', 'marked.min.js'));
  const hljsJs = readAsset(join('vendor', 'highlight.min.js'));

  const themeVars = Object.entries(theme.cssColors)
    .map(([key, value]) => `--${key}: ${value};`)
    .join('\n      ');
  const themeVarsWithExport = [
    themeVars,
    `--exportPageBg: ${theme.pageBg};`,
    `--exportCardBg: ${theme.cardBg};`,
    `--exportInfoBg: ${theme.infoBg};`,
  ].join('\n      ');

  const css = templateCss
    .replace('{{THEME_VARS}}', () => themeVarsWithExport)
    .replace('{{BODY_BG}}', () => theme.pageBg)
    .replace('{{CONTAINER_BG}}', () => theme.cardBg)
    .replace('{{INFO_BG}}', () => theme.infoBg);

  const sessionDataBase64 = Buffer.from(JSON.stringify(sessionData)).toString('base64');

  // 函数形态替换：minified JS 里的 $ 序列（$&/$' 等）在字符串替换形态下
  // 会被当作特殊模式展开——函数返回值不做 $ 解释
  return template
    .replace('{{CSS}}', () => css)
    .replace('{{JS}}', () => templateJs)
    .replace('{{SESSION_DATA}}', () => sessionDataBase64)
    .replace('{{MARKED_JS}}', () => markedJs)
    .replace('{{HIGHLIGHT_JS}}', () => hljsJs);
}
