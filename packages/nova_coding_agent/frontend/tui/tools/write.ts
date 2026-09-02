/**
 * write 工具渲染器（新建组件——pi write.ts 渲染语义对位）。
 *
 * details 契约（backend/tools/write.py）：错误时为 { error, path? }；
 * 成功只有文本回执。内容取自**参数**（args.content——write 的正文在参数里）。
 *
 * 呈现语义（pi 对齐）：
 * - 折叠时显示前 10 行内容预览 + `... (N more lines, ctrl+o to expand)`；
 * - 展开时全量；content 缺失/非字符串给明确提示行；
 * - 成功结果不追加内容（错误时显示错误行）。
 */
import { Container, Text, type Component } from '@earendil-works/pi-tui';

import { detailsOf, type RendererInput } from 'nova-client';

/** 折叠预览行数（pi write 卡片同款）。 */
const PREVIEW_LINES = 10;

export default function renderWrite(input: RendererInput): Component {
  const d = detailsOf(input);
  const colors = input.env?.colors;
  const dim = (s: string) => colors?.dim?.(s) ?? s;
  const err = (s: string) => colors?.error?.(s) ?? s;
  const expanded = input.env?.expanded === true;

  const container = new Container();

  if (typeof d.error === 'string' && d.error) {
    container.addChild(new Text(err(`写入失败：${d.error}`), 1, 0));
    return container;
  }

  // 正文取自参数（write 的内容在 args.content）
  const args = (input.args ?? {}) as Record<string, unknown>;
  const content = args.content;
  if (typeof content !== 'string') {
    if (input.status === 'done' && content === undefined) return container; // 成功且无内容——静默
    container.addChild(new Text(err('[invalid content arg - expected string]'), 1, 0));
    return container;
  }

  const lines = content.split('\n');
  if (expanded || lines.length <= PREVIEW_LINES) {
    container.addChild(new Text(content, 1, 0));
  } else {
    container.addChild(new Text(lines.slice(0, PREVIEW_LINES).join('\n'), 1, 0));
    container.addChild(
      new Text(
        dim(`... (${lines.length - PREVIEW_LINES} more lines, ${lines.length} total, ctrl+o to expand)`),
        1,
        0,
      ),
    );
  }
  return container;
}
