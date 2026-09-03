/**
 * OverlayHost：overlay 浮层宿主（registerOverlay 的 TUI 消费点）。
 *
 * ``region:overlay`` slot 的信封（组件 + 布局选项）在本组件内解包，
 * 经 ``tui.showOverlay`` 叠画到整个布局之上；slot 消失/替换时
 * hide 旧 overlay 并重挂。组件本身**零高度**（render 恒返空数组）——
 * 只是借渲染周期做生命周期管理（RegionHost 的同构变体）。
 *
 * 裸组件直注 ``region:overlay``（未经 registerOverlay 包装）也接受——
 * 走 pi-tui 默认布局（center 锚点）。部件异常静默（不炸宿主布局）。
 */

import type { NovaUIRuntime } from 'nova-tui';
import { regionSlot, unwrapOverlay, type NovaOverlayOptions } from 'nova-tui';
import type { Component, OverlayHandle, OverlayOptions, TUI } from '@earendil-works/pi-tui';

import type { RegionEnv } from './region-host.js';

type OverlayFn = (env: RegionEnv) => unknown;

/** NovaOverlayOptions → pi-tui OverlayOptions（字段同构，直接透传）。 */
function toPiOptions(options: NovaOverlayOptions | undefined): OverlayOptions | undefined {
  return options as OverlayOptions | undefined;
}

export class OverlayHost implements Component {
  private activeFn: OverlayFn | undefined;
  private handle: OverlayHandle | undefined;

  constructor(
    private readonly runtime: NovaUIRuntime,
    private readonly tui: TUI,
    private readonly env: RegionEnv,
  ) {}

  /** 零高度：借每帧渲染做 slot 对账（出现/替换/消失三个迁移点）。 */
  render(_width: number): string[] {
    const fn = this.runtime.slots.resolve<RegionEnv, unknown>(regionSlot('overlay')) as
      | OverlayFn
      | undefined;
    if (!fn) {
      this.hideActive();
      return [];
    }
    if (fn !== this.activeFn) this.adopt(fn);
    return [];
  }

  /** Component 接口要求（无缓存状态——空实现）。 */
  invalidate(): void {}

  /** 首次/变更接入：建厂 → 解包信封 → showOverlay。 */
  private adopt(fn: OverlayFn): void {
    this.hideActive();
    this.activeFn = fn;
    let out: unknown;
    try {
      out = fn(this.env);
    } catch {
      return; // 建厂即抛错——保持空态（下帧重试）
    }
    const unwrapped = unwrapOverlay(out);
    const candidate = unwrapped ? unwrapped.component : out;
    if (
      typeof candidate !== 'object' ||
      candidate === null ||
      typeof (candidate as { render?: unknown }).render !== 'function'
    ) {
      return; // 非组件形态：空态（下帧重试）
    }
    try {
      this.handle = this.tui.showOverlay(
        candidate as Component,
        toPiOptions(unwrapped?.options),
      );
    } catch {
      this.handle = undefined; // showOverlay 异常静默
    }
  }

  private hideActive(): void {
    this.activeFn = undefined;
    try {
      this.handle?.hide();
    } catch {
      // hide 异常静默
    }
    this.handle = undefined;
  }
}
