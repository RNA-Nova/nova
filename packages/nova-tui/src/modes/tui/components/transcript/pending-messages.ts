/**
 * 排队消息区。
 *
 * 数据源：快照的 steering_messages / follow_up_messages（字符串数组）。
 * 有排队内容时显示在转录区与状态区之间（dim 预览），空则隐藏。
 * 呈现：前缀标记（steer ⟶ / follow-up ⏎）+ 单行截断预览。
 */

import type { NovaUIRuntime } from 'nova-tui';
import { Container, Text, type Component } from '@earendil-works/pi-tui';

import { colors } from '../../themes/index.js';

function preview(text: string, limit = 80): string {
  const first = text.trim().split('\n', 1)[0] ?? '';
  return first.length > limit ? `${first.slice(0, limit)}…` : first;
}

export class PendingMessagesView implements Component {
  constructor(private readonly runtime: NovaUIRuntime) {}

  invalidate(): void {}

  render(width: number): string[] {
    const snapshot = this.runtime.store.currentSnapshot;
    if (!snapshot) return [];

    const steering = snapshot.steeringMessages ?? [];
    const followUp = snapshot.followUpMessages ?? [];
    if (steering.length === 0 && followUp.length === 0) return [];

    const lines: string[] = [];
    for (const message of steering) {
      lines.push(colors.dim(`⟶ steer: ${preview(message, Math.max(20, width - 14))}`));
    }
    for (const message of followUp) {
      lines.push(colors.dim(`⏎ follow-up: ${preview(message, Math.max(20, width - 14))}`));
    }
    return lines;
  }
}

/** 槽位容器（供 app 组装：排队区仅在 render 有内容时占行）。 */
export function createPendingSlot(runtime: NovaUIRuntime): Container {
  const container = new Container();
  container.addChild(new PendingMessagesView(runtime));
  return container;
}
