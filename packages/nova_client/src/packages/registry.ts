/**
 * PackageRegistry：已安装包索引（packages/ 子系统，设计 v3 §8）。
 *
 * 职责边界：本模块**不是包管理器**——装/卸/更新的物理生命周期全在
 * Python nova-pkg；这里只是本层对包世界的**索引与感知**：
 * 扩展宿主靠它发现 ``ui/`` 资产，包面板（M4）靠它出数据。
 *
 * 数据源：``pkgList`` RPC（{identity: PackageView}），缓存 + 显式失效。
 */

import type { WireClient } from '../wire/client.js';

/** 一个已安装包的索引条目（pkgList PackageView 的本层投影）。 */
export interface InstalledPackageInfo {
  /** 包身份（source 派生的去重键）。 */
  identity: string;
  name: string;
  version: string;
  description: string;
  /** 安装路径（copy 副本或 editable 源目录）——ui/ 资产发现的入口。 */
  installPath: string;
  scope: 'user' | 'project';
}

/** pkgList 返回形状（线上 camelCase——防御性解析）。 */
interface RawPackageView {
  name?: unknown;
  version?: unknown;
  description?: unknown;
  installPath?: unknown;
  scope?: unknown;
}

function parseView(identity: string, raw: unknown): InstalledPackageInfo | null {
  const view = (typeof raw === 'object' && raw !== null ? raw : {}) as RawPackageView;
  if (typeof view.installPath !== 'string' || !view.installPath) return null;
  return {
    identity,
    name: typeof view.name === 'string' ? view.name : identity,
    version: typeof view.version === 'string' ? view.version : '',
    description: typeof view.description === 'string' ? view.description : '',
    installPath: view.installPath,
    scope: view.scope === 'project' ? 'project' : 'user',
  };
}

export class PackageRegistry {
  private packages: InstalledPackageInfo[] = [];

  constructor(private readonly client: WireClient) {}

  /** 重新拉取已安装包索引（装/卸/更新后由调用方触发）。 */
  async refresh(): Promise<readonly InstalledPackageInfo[]> {
    const result = (await this.client.call('pkgList', { local: false })) as Record<
      string,
      unknown
    >;
    const parsed: InstalledPackageInfo[] = [];
    for (const [identity, raw] of Object.entries(result ?? {})) {
      const info = parseView(identity, raw);
      if (info !== null) parsed.push(info);
    }
    this.packages = parsed;
    return this.list();
  }

  /** 当前缓存的包索引（首次访问前需 refresh）。 */
  list(): readonly InstalledPackageInfo[] {
    return this.packages;
  }
}
