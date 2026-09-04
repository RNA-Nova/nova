/**
 * 状态指示器家族。
 *
 * 基类 extends pi-tui Loader（spinner 动画内建）；四变体：
 * - WorkingStatusIndicator：spinner + 工作中文案；
 * - RetryStatusIndicator：CountdownTimer 倒计时
 *   （"Retrying (2/3) in 5s… (esc to cancel)"）；
 * - CompactionStatusIndicator：触发原因文案（manual/threshold/overflow）；
 * - BranchSummaryStatusIndicator：分支摘要中。
 *
 * 文案均带键位提示（hints 从键位表动态生成，不写死）。
 */

import { Loader, type LoaderIndicatorOptions, type TUI } from '@earendil-works/pi-tui';

import { colors } from '../../themes/index.js';
import { keyText } from '../pickers/hints.js';
import { CountdownTimer } from './countdown-timer.js';

export type StatusIndicatorKind = 'working' | 'retry' | 'compaction' | 'branchSummary';

export class StatusIndicator extends Loader {
  readonly kind: StatusIndicatorKind;

  constructor(
    kind: StatusIndicatorKind,
    ui: TUI,
    spinnerColorFn: (str: string) => string,
    messageColorFn: (str: string) => string,
    message: string,
    indicator?: LoaderIndicatorOptions,
  ) {
    super(ui, spinnerColorFn, messageColorFn, message, indicator);
    this.kind = kind;
  }

  /** 释放（Loader 无 dispose——stop 即清理；变体有附加资源时 override）。 */
  dispose(): void {
    this.stop();
  }
}

export class WorkingStatusIndicator extends StatusIndicator {
  constructor(
    ui: TUI,
    message = 'Working…',
    indicator?: LoaderIndicatorOptions,
  ) {
    super('working', ui, colors.accent, colors.muted, message, indicator);
  }
}

export class RetryStatusIndicator extends StatusIndicator {
  private countdown: CountdownTimer | undefined;

  constructor(ui: TUI, attempt: number, maxAttempts: number, delayMs: number) {
    const retryMessage = (seconds: number) =>
      `Retrying (${attempt}/${maxAttempts}) in ${seconds}s… (${keyText('tui.select.cancel')} to cancel)`;
    super('retry', ui, colors.warning, colors.muted, retryMessage(Math.ceil(delayMs / 1000)));
    this.countdown = new CountdownTimer(
      delayMs,
      ui,
      (seconds) => this.setMessage(retryMessage(seconds)),
      () => {
        this.countdown = undefined;
      },
    );
  }

  override dispose(): void {
    this.countdown?.dispose();
    this.countdown = undefined;
    super.dispose();
  }
}

export class CompactionStatusIndicator extends StatusIndicator {
  constructor(ui: TUI, reason: string | null) {
    const cancelHint = `(${keyText('tui.select.cancel')} to cancel)`;
    const label =
      reason === 'manual'
        ? `Compacting context… ${cancelHint}`
        : `${reason === 'overflow' ? 'Context overflow detected, ' : ''}Auto-compacting… ${cancelHint}`;
    super('compaction', ui, colors.accent, colors.muted, label);
  }
}

export class BranchSummaryStatusIndicator extends StatusIndicator {
  constructor(ui: TUI) {
    super(
      'branchSummary',
      ui,
      colors.accent,
      colors.muted,
      `Summarizing branch… (${keyText('tui.select.cancel')} to cancel)`,
    );
  }
}
