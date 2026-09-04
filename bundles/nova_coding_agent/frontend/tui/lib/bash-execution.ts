/**
 * bashExecution 条目卡片（**包侧**渲染器——user_tools/bash 的消息类型
 * 归包呈现，宿主 transcript 只消费 entry:<customType> 槽，不内置本卡）。
 *
 * （适配 custom 条目数据）：
 * 数据源 BashExecutionMessage（command/output/exitCode/cancelled/
 * truncated/fullOutputPath/excludeFromContext——线上 camel）。
 *
 * 视觉：DynamicBorder 上下框 + `$ command` 头 + 输出（折叠预览末 20 行）
 * + 状态行（cancelled/exit code/截断提示）。
 *
 * 更新语义：流式期条目以空数据创建、user_tool chunk 与 message_end
 * 陆续定稿——宿主在条目数据变化时回调 ``update(data)`` 整体重绘
 *，组件身份不变。
 */

import { Container, Spacer, Text } from '@earendil-works/pi-tui';
import { DynamicBorder } from 'nova-tui/modes/tui/components/layout/dynamic-border';
import { colors } from 'nova-tui/modes/tui/themes/index';

const PREVIEW_LINES = 20;

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

export class BashExecutionCard extends Container {
  constructor(data: unknown) {
    super();
    this.update(data);
  }

  /** 按最新条目数据整体重绘（构造与定稿共用）。 */
  update(data: unknown): void {
    const message = asRecord(data);
    const command = str(message.command);
    const output = str(message.output);
    // 线上 camel（dump_wire）——勿用 snake 键读取（会静默全丢）
    const exitCode = typeof message.exitCode === 'number' ? message.exitCode : undefined;
    const cancelled = message.cancelled === true;
    const truncated = message.truncated === true;
    const fullOutputPath = str(message.fullOutputPath);
    const excludeFromContext = message.excludeFromContext === true;

    const colorKey = excludeFromContext ? colors.dim : colors.bashMode;

    this.clear();

    this.addChild(new Spacer(1));
    this.addChild(new DynamicBorder(colorKey));

    // 命令头
    this.addChild(new Text(colorKey(`$ ${command}`), 1, 0));

    // 输出（折叠预览末 20 行）
    const lines = output ? output.split('\n') : [];
    const hidden = Math.max(0, lines.length - PREVIEW_LINES);
    const preview = hidden > 0 ? lines.slice(-PREVIEW_LINES) : lines;
    if (preview.length > 0) {
      this.addChild(new Text(preview.map((l) => colors.muted(l)).join('\n'), 1, 0));
    }

    // 状态行
    const statusParts: string[] = [];
    if (hidden > 0) statusParts.push(colors.muted(`... ${hidden} more lines`));
    if (cancelled) statusParts.push(colors.warning('(cancelled)'));
    else if (exitCode !== undefined && exitCode !== 0)
      statusParts.push(colors.error(`(exit ${exitCode})`));
    if (truncated && fullOutputPath)
      statusParts.push(colors.warning(`Output truncated. Full output: ${fullOutputPath}`));
    if (statusParts.length > 0) {
      this.addChild(new Text(statusParts.join('\n'), 1, 0));
    }

    this.addChild(new DynamicBorder(colorKey));
  }
}
