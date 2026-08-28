/**
 * 构建后资产复制：tsc 不复制非 ts 文件——export 模板资产
 * （template.html/css/js + vendor/*.js）拷到 dist 保持运行时相对路径成立；
 * 仓库根 CHANGELOG.md 拷到 dist/assets/（/changelog 与 What's New 的运行时数据源）。
 */

import { cpSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const srcExport = join(root, '..', 'src', 'export');
const distExport = join(root, '..', 'dist', 'export');

mkdirSync(distExport, { recursive: true });
for (const name of ['template.html', 'template.css', 'template.js']) {
  cpSync(join(srcExport, name), join(distExport, name));
}
mkdirSync(join(distExport, 'vendor'), { recursive: true });
for (const name of ['marked.min.js', 'highlight.min.js']) {
  cpSync(join(srcExport, 'vendor', name), join(distExport, 'vendor', name));
}
console.log('export assets copied to dist/export/');

// 仓库根 CHANGELOG.md（monorepo 单一出处）→ dist/assets/
const distAssets = join(root, '..', 'dist', 'assets');
mkdirSync(distAssets, { recursive: true });
cpSync(join(root, '..', '..', '..', '..', 'CHANGELOG.md'), join(distAssets, 'CHANGELOG.md'));
console.log('CHANGELOG.md copied to dist/assets/');
