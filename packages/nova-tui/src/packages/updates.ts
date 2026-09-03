/**
 * 包更新提醒（packages/ 子系统，设计 v3 §8 updates.ts）。
 *
 * pi 同款拉模式：启动时前端顺手拉一次可更新列表——只读、离线/失败静默。
 * 本模块持有全部产品逻辑（查什么、怎么格式化、失败怎么办），facade
 * 只做"拿到文本 → 进 transcript"一步编排。
 */

import type { WireClient } from '../wire/client.js';

/**
 * 拉取可更新包并格式化为提醒文本；无可更新/失败/离线返回 null。
 * （``pkgCheckUpdates`` 只读，永远不阻塞启动。）
 */
export async function fetchPackageUpdateNotice(client: WireClient): Promise<string | null> {
  try {
    const result = await client.call('pkgCheckUpdates', {});
    const updates = result.updates ?? [];
    if (updates.length === 0) return null;
    const names = updates
      .map((u) => u.displayName ?? '')
      .filter(Boolean)
      .join(', ');
    return `${String(updates.length)} 个包可更新：${names}（运行 nova-pkg update 更新）`;
  } catch {
    return null;
  }
}
