/**
 * 内建包面板（R17 dogfood 验收——应用内包管理本身经 ExtensionUIAPI 实现）。
 *
 * 形态：/packages 命令（registerCommand 内建注册，source=builtin——
 * 与第三方扩展同一 API 通道）→ 包列表（ctx.select 选择器原语）→
 * 详情/更新/卸载动作。扩展 API 强到能长出我们自己的包面板，才算完备。
 */

import type { ExtensionUIAPI, ExtensionUIContext } from 'nova-tui';

interface PackageViewLite {
  name: string;
  version?: string;
  description?: string;
  scope?: string;
  tools?: unknown[];
  skills?: unknown[];
  agents?: unknown[];
}

function packagesOf(result: unknown): PackageViewLite[] {
  if (typeof result !== 'object' || result === null) return [];
  return Object.values(result as Record<string, unknown>)
    .filter((item): item is PackageViewLite => typeof item === 'object' && item !== null)
    .map((item) => ({
      name: String(item.name ?? ''),
      version: typeof item.version === 'string' ? item.version : undefined,
      description: typeof item.description === 'string' ? item.description : undefined,
      scope: typeof item.scope === 'string' ? item.scope : undefined,
      tools: Array.isArray(item.tools) ? item.tools : [],
      skills: Array.isArray(item.skills) ? item.skills : [],
      agents: Array.isArray(item.agents) ? item.agents : [],
    }))
    .filter((item) => item.name);
}

async function openPanel(ctx: ExtensionUIContext, local: boolean): Promise<void> {
  const result = await ctx.invoke('pkgList', { local });
  const packages = packagesOf(result);
  if (packages.length === 0) {
    ctx.notify('没有已安装的包（nova-pkg install 安装）');
    return;
  }

  // 更新角标（失败静默——无角标降级）
  const outdated = new Set<string>();
  try {
    const check = (await ctx.invoke('pkgCheckUpdates', {})) as {
      updates?: Array<{ source?: string; displayName?: string }>;
    };
    for (const update of check.updates ?? []) {
      if (update.displayName) outdated.add(update.displayName);
    }
  } catch {
    // 无角标
  }

  const chosen = await ctx.select!('已安装的包', [
    ...packages.map((pkg) => ({
      value: pkg.name,
      label: `${pkg.name}${outdated.has(pkg.name) ? ' ↑' : ''}`,
      description: `${pkg.scope ?? 'user'} · v${pkg.version ?? '?'} · ${
        (pkg.tools?.length ?? 0) + (pkg.skills?.length ?? 0) + (pkg.agents?.length ?? 0)
      } 项资源`,
    })),
    {
      value: '__toggle_scope__',
      label: local ? '切换到项目级' : '切换到用户级',
      description: `当前：${local ? '项目' : '用户'}级列表`,
    },
  ]);
  if (chosen === undefined) return;
  if (chosen === '__toggle_scope__') {
    await openPanel(ctx, !local);
    return;
  }

  const pkg = packages.find((candidate) => candidate.name === chosen);
  if (!pkg) return;
  const action = await ctx.select!(
    `${pkg.name} v${pkg.version ?? '?'}`,
    [
      { value: 'detail', label: '详情', description: pkg.description ?? '' },
      { value: 'update', label: '更新', description: '拉取最新版本' },
      { value: 'uninstall', label: '卸载', description: '移除该包（资源随之消失）' },
    ],
  );
  if (action === undefined) return;

  if (action === 'detail') {
    ctx.notify(
      `${pkg.name} v${pkg.version ?? '?'}（${pkg.scope ?? 'user'}）\n${pkg.description ?? '无描述'}`,
    );
    return;
  }
  if (action === 'update') {
    await ctx.invoke('pkgUpdate', { name: pkg.name });
    await ctx.refreshPackages?.();
    ctx.notify(`已更新 ${pkg.name}`);
    return;
  }
  if (action === 'uninstall') {
    await ctx.invoke('pkgUninstall', { name: pkg.name });
    await ctx.refreshPackages?.();
    ctx.notify(`已卸载 ${pkg.name}（资源已移除）`);
  }
}

/** 注册内建包面板命令（slotsBootstrap 调用——内建与第三方同一 API）。 */
export function registerPackagePanel(api: ExtensionUIAPI): void {
  api.registerCommand('packages', {
    description: '包管理面板（列表/详情/更新/卸载）',
    handler: async (_args, ctx) => {
      if (!ctx.select) {
        ctx.notify('包面板需要 UI 选择器通道（当前宿主未注入）', 'warning');
        return;
      }
      try {
        await openPanel(ctx, false);
      } catch (error) {
        ctx.notify(`包面板出错：${error instanceof Error ? error.message : String(error)}`, 'error');
      }
    },
  });
}
