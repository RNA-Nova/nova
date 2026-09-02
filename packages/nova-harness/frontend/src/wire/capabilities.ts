/**
 * 契约版本与能力位模型（wire 层）。
 *
 * 版本语义（R6）：
 * - **major 不等 → 硬拒**（握手响亮失败——错配会在字段缺失时才静默爆炸）；
 * - **minor 差 → 放行**：minor 只表达加法变更，未知事件/方法由
 *   "联合外静默忽略 + 能力位降级"兜底。
 *
 * 能力位：后端握手时宣告自己支持的方法域/方法清单（多后端设计——
 * 某后端未实现的域，前端按位隐藏入口而不是报错）。
 */

import { NOVA_CONTRACT_MAJOR } from '../protocol/nova-wire.gen.js';

/** initialize 握手响应的最小形状（本模块唯一关心的字段）。 */
export interface HandshakeInfo {
  version?: string;
  contractVersionMajor?: number;
  contractVersionMinor?: number;
  capabilities?: { domains?: string[]; methods?: string[] };
}

/**
 * 校验后端契约版本与本端 major 一致。
 * major 缺失或不等 → throw；minor 任意差 → 放行。
 */
export function checkContractVersion(handshake: HandshakeInfo): void {
  const remoteMajor = handshake.contractVersionMajor;
  if (remoteMajor !== NOVA_CONTRACT_MAJOR) {
    throw new Error(
      `后端契约 major 版本（${String(remoteMajor)}）与本端` +
        `（${String(NOVA_CONTRACT_MAJOR)}）不兼容——请对齐后端/前端版本`,
    );
  }
}

/** 后端能力位集合：域/方法两级查询（前端按位降级）。 */
export class CapabilitySet {
  private readonly domains: Set<string>;
  private readonly methods: Set<string>;

  constructor(handshake: HandshakeInfo) {
    this.domains = new Set(handshake.capabilities?.domains ?? []);
    this.methods = new Set(handshake.capabilities?.methods ?? []);
  }

  /** 整个方法域是否可用（如 ``package``——无此后端的前端隐藏包面板入口）。 */
  hasDomain(domain: string): boolean {
    return this.domains.has(domain);
  }

  /** 单个方法是否可用。 */
  hasMethod(method: string): boolean {
    return this.methods.has(method);
  }
}
