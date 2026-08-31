/**
 * 可复用倒计时器（pi countdown-timer.ts 对位）。
 *
 * 用途：对话框 timeout 倒计时（transport timeout_ms 的前端呈现）、
 * retry 指示器的"in Ns"倒计时。每秒 tick + 到期回调。
 */

import type { TUI } from '@earendil-works/pi-tui';

export class CountdownTimer {
  private intervalId: ReturnType<typeof setInterval> | undefined;
  private remainingSeconds: number;
  private tui: TUI | undefined;
  private onTick: (seconds: number) => void;
  private onExpire: () => void;

  constructor(
    timeoutMs: number,
    tui: TUI | undefined,
    onTick: (seconds: number) => void,
    onExpire: () => void,
  ) {
    this.tui = tui;
    this.onTick = onTick;
    this.onExpire = onExpire;
    this.remainingSeconds = Math.ceil(timeoutMs / 1000);
    this.onTick(this.remainingSeconds);

    this.intervalId = setInterval(() => {
      this.remainingSeconds--;
      this.onTick(this.remainingSeconds);
      this.tui?.requestRender();

      if (this.remainingSeconds <= 0) {
        this.dispose();
        this.onExpire();
      }
    }, 1000);
  }

  dispose(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = undefined;
    }
  }
}
