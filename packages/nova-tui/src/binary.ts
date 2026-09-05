/**
 * 编译二进制（bun --compile）形态探测与随行资产基底。
 *
 * 编译态 ``import.meta.url`` 指向 bunfs 虚拟文件系统（Windows 为 ~BUN 前缀），
 * 一切由 import.meta.url 派生的磁盘路径都会落空——资产走随行文件：构建脚本
 * （scripts/build-frontend.sh）把 CHANGELOG.md / package.json / export/ /
 * native/ 拷到二进制旁，运行时的资产基底即 ``dirname(process.execPath)``。
 */

import { dirname } from 'node:path';

/** bun --compile 二进制检测（bunfs 虚拟文件系统路径标记）。 */
export const isBunBinary =
  import.meta.url.includes('$bunfs') ||
  import.meta.url.includes('~BUN') ||
  import.meta.url.includes('%7EBUN');

/** 随行资产基底目录（仅编译形态有意义——二进制所在目录）。 */
export function binaryAssetBase(): string {
  return dirname(process.execPath);
}
