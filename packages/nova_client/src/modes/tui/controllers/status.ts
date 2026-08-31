/**
 * StatusController：statusContainer 槽位管理（状态指示器家族 + 通知）。
 *
 * 驱动源：
 * - 指示器：store.status（working/retrying/compacting ↔ idle）+ 状态详情
 *   （retryStatus 倒计时 / compactionReason 文案）；
 * - notice：DialogController 转交的 ui/notify（仅 idle 时显示，
 *   避免与指示器争槽位）。
 */

import type { NovaUIRuntime } from 'nova-client';
import { Container, Text, type LoaderIndicatorOptions, type TUI } from '@earendil-works/pi-tui';

import {
  CompactionStatusIndicator,
  RetryStatusIndicator,
  StatusIndicator,
  WorkingStatusIndicator,
} from '../components/status/indicators.js';
import { colors } from '../themes/index.js';

export class StatusController {
  private indicator: StatusIndicator | undefined;
  /** working 三旋钮（pi setWorking* 对位——扩展定制内建 loader）。 */
  private workingMessage: string | undefined;
  private workingIndicator: LoaderIndicatorOptions | undefined;
  private workingVisible = true;

  constructor(
    private readonly tui: TUI,
    private readonly container: Container,
    private readonly runtime: NovaUIRuntime,
  ) {
    this.runtime.store.subscribe((change) => {
      if (change.area === 'status') this.refresh();
    });
  }

  /** 工作中文案（undefined 恢复默认）。 */
  setWorkingMessage(message?: string): void {
    this.workingMessage = message;
    this.recreate();
  }

  /** spinner 帧/间隔（undefined 恢复默认；frames: [] 隐藏帧）。 */
  setWorkingIndicator(options?: LoaderIndicatorOptions): void {
    this.workingIndicator = options;
    this.recreate();
  }

  /** 显示/隐藏内建 working loader 行。 */
  setWorkingVisible(visible: boolean): void {
    this.workingVisible = visible;
    this.recreate();
  }

  /** 旋钮变更后按当前状态重建指示器（同变体续用路径必须绕过）。 */
  private recreate(): void {
    const current = this.indicator;
    if (current === undefined) return;
    current.dispose();
    this.indicator = undefined;
    this.container.clear();
    const next = this.createIndicator(this.runtime.store.status);
    if (next !== undefined) {
      this.indicator = next;
      this.container.addChild(next);
    }
    this.tui.requestRender();
  }

  /** 按 store.status 刷新指示器（idle 停、工作态起；状态迁移时切换变体）。 */
  refresh(): void {
    const status = this.runtime.store.status;
    const next = this.createIndicator(status);

    if (next === undefined && this.indicator === undefined) return;
    // 同变体续用（避免闪烁）；变体切换/进入 idle 时替换
    if (
      next !== undefined &&
      this.indicator !== undefined &&
      next.kind === this.indicator.kind
    ) {
      return;
    }

    this.indicator?.dispose();
    this.indicator = next;
    this.container.clear();
    if (this.indicator !== undefined) {
      this.container.addChild(this.indicator);
    }
    this.tui.requestRender();
  }

  /** 按状态创建对应指示器（idle → undefined；working 受三旋钮约束）。 */
  private createIndicator(status: string): StatusIndicator | undefined {
    switch (status) {
      case 'working':
        if (!this.workingVisible) return undefined;
        return new WorkingStatusIndicator(
          this.tui,
          this.workingMessage ?? 'Working…',
          this.workingIndicator,
        );
      case 'retrying': {
        const retry = this.runtime.store.retryStatus;
        return new RetryStatusIndicator(
          this.tui,
          retry?.attempt ?? 1,
          retry?.maxAttempts ?? 1,
          retry?.delayMs ?? 0,
        );
      }
      case 'compacting':
        return new CompactionStatusIndicator(
          this.tui,
          this.runtime.store.compactionReason,
        );
      default:
        return undefined;
    }
  }

  /** ui/notify 通知显示（仅 idle 时占用槽位，不遮指示器）。 */
  showNotice(message: string, type: string): void {
    if (this.indicator !== undefined) return; // 指示器优先
    const color =
      type === 'error' ? colors.error : type === 'warning' ? colors.warning : colors.dim;
    this.container.clear();
    this.container.addChild(new Text(color(message), 1, 0));
    this.tui.requestRender();
  }

  /** 清除通知槽位（空 progress 通知——如登录流程结束清掉 Waiting 提示）。 */
  clearNotice(): void {
    this.container.clear();
    this.tui.requestRender();
  }
}
