/**
 * 版本戳（单一事实源：package.json 的 ``version`` 字段，构建期注入只是它的搬运）。
 *
 * 三形态解析：
 * - bun --compile 二进制：构建期经 ``--define __NOVA_VERSION__`` 注入
 *   （编译态 import.meta.url 指向 bunfs，读不到磁盘 package.json）；
 * - 注入缺席的编译态兜底：读二进制旁随行的 package.json
 *   （scripts/build-frontend.sh 拷贝）；
 * - node/tsx 形态：读包根 package.json（src/version.ts 与 dist/version.js
 *   到包根同为上 1 级，双形态一致）。
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { binaryAssetBase, isBunBinary } from './binary.js';

// 构建期注入点（scripts/build-frontend.sh 的 bun --define；node/tsx 形态无
// 定义——typeof 守卫防 ReferenceError，declare 不产生运行时代码）
declare const __NOVA_VERSION__: string | undefined;

/** 读指定目录下 package.json 的 version 字段；不可得返回空串。 */
function readVersionAt(dir: string): string {
  try {
    const pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf-8')) as {
      version?: unknown;
    };
    return typeof pkg.version === 'string' ? pkg.version : '';
  } catch {
    return '';
  }
}

function resolveVersion(): string {
  if (typeof __NOVA_VERSION__ === 'string' && __NOVA_VERSION__ !== '') {
    return __NOVA_VERSION__;
  }
  if (isBunBinary) {
    return readVersionAt(binaryAssetBase());
  }
  return readVersionAt(fileURLToPath(new URL('..', import.meta.url)));
}

/** 当前前端版本（nova-tui 包版本；读不到为空串——调用方判空降级）。 */
export const NOVA_VERSION: string = resolveVersion();
