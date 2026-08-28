/**
 * 呈现资源的 trust 过滤（resources/ 子系统）。
 *
 * pi 发现域过滤思想对位：project 级包在项目不被信任时整包剔除——
 * 不 stat、不 import（比加载器跳过更上游：不被信任的代码连发现域
 * 都不进）。user 级包恒放行（全局安装是主动行为）。
 *
 * trust 决议来自后端快照（getSessionState.projectTrusted）——本层
 * 只按决议过滤，不编排决议（bootstrap/裁决归 nova_harness）。
 */

import type { PackageUIAssets } from './types.js';

/** 按 trust 决议把资产分为"获准加载"与"剔除"（skipped 透出供诊断）。 */
export function partitionByTrust(
  assets: PackageUIAssets[],
  projectTrusted: boolean,
): { allowed: PackageUIAssets[]; skipped: string[] } {
  const allowed: PackageUIAssets[] = [];
  const skipped: string[] = [];
  for (const asset of assets) {
    if (asset.scope === 'project' && !projectTrusted) {
      skipped.push(asset.packageName);
    } else {
      allowed.push(asset);
    }
  }
  return { allowed, skipped };
}
