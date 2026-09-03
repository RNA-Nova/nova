/**
 * RegionHost：区域部件宿主（逃生舱泛化的消费点）。
 *
 * ``region:<name>`` slot 的两态生产者在同一组件内分流：
 * - **声明式**（默认轨）：producer 每帧调用（纯函数便宜）产 NovaBlock[]，
 *   输出指纹不变复用适配组件（Markdown 构造贵）——可过网（M3 远程）；
 * - **组件工厂**（逃生舱）：首次解析时建厂一次，产出 pi-tui Component
 *   直挂（有状态/可交互——pi setWidget/setFooter 的对位）；同进程全自由。
 *
 * 判别：注册函数首次调用的返回形态（数组 → 块；否则 → 组件）。
 * slots 整体替换（refreshPackages）后下次渲染自动重判别（函数引用比对）。
 * 部件异常静默（不炸宿主布局）。
 */

import type { NovaBlock, NovaUIRuntime, RegionContext } from 'nova-tui';
import { regionSlot } from 'nova-tui';
import type { Component, TUI } from '@earendil-works/pi-tui';

import { blocksToComponents } from '../../blocks/index.js';
import { colors, markdownTheme } from '../../themes/index.js';

/** 区域部件环境（声明式与逃生舱共用——producer 用 cwd，factory 用全套）。 */
export interface RegionEnv extends RegionContext {
  tui: TUI;
  colors: typeof colors;
  markdownTheme: typeof markdownTheme;
}

type RegionFn = (env: RegionEnv) => unknown;

export class RegionHost implements Component {
  private activeFn: RegionFn | undefined;
  private mode: 'blocks' | 'component' | undefined;
  private component: Component | undefined;
  private blockComponents: Component[] | undefined;
  private fingerprint: string | undefined;

  constructor(
    private readonly runtime: NovaUIRuntime,
    private readonly region: string,
    private readonly env: RegionEnv,
  ) {}

  render(width: number): string[] {
    const fn = this.runtime.slots.resolve<RegionEnv, unknown>(regionSlot(this.region)) as
      | RegionFn
      | undefined;
    if (!fn) {
      this.reset();
      return [];
    }
    if (fn !== this.activeFn) this.adopt(fn); // 新注册/替换 → 重判别
    try {
      if (this.mode === 'component') {
        return this.component?.render(width) ?? [];
      }
      return this.renderBlocks(fn, width);
    } catch {
      return []; // 部件异常静默
    }
  }

  invalidate(): void {
    this.component?.invalidate?.();
  }

  /** 首次/变更接入：调用一次判别形态（数组 → 声明式；否则 → 组件）。 */
  private adopt(fn: RegionFn): void {
    this.reset();
    this.activeFn = fn;
    let out: unknown;
    try {
      out = fn(this.env);
    } catch {
      return; // 建厂即抛错——保持空态（下帧重试判别）
    }
    if (Array.isArray(out)) {
      this.mode = 'blocks';
      this.applyBlocks(out as NovaBlock[]);
    } else if (
      typeof out === 'object' &&
      out !== null &&
      typeof (out as { render?: unknown }).render === 'function'
    ) {
      this.mode = 'component';
      this.component = out as Component;
    }
    // 其他返回形态：空态（下帧重试）
  }

  private renderBlocks(fn: RegionFn, width: number): string[] {
    const out = fn(this.env);
    if (!Array.isArray(out) || out.length === 0) return [];
    this.applyBlocks(out as NovaBlock[]);
    return (this.blockComponents ?? []).flatMap((component) => component.render(width));
  }

  /** 块列表 → 组件（指纹比对——输出不变复用）。 */
  private applyBlocks(blocks: NovaBlock[]): void {
    const fingerprint = JSON.stringify(blocks);
    if (fingerprint === this.fingerprint) return;
    this.fingerprint = fingerprint;
    this.blockComponents = blocksToComponents(blocks, this.runtime.slots);
  }

  private reset(): void {
    this.activeFn = undefined;
    this.mode = undefined;
    this.component = undefined;
    this.blockComponents = undefined;
    this.fingerprint = undefined;
  }
}
