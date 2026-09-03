/**
 * ls 工具渲染器（新建组件——pi ls.ts 渲染语义对位）。
 *
 * 结果文本在 result.content（每行一个条目，目录带尾 `/`）。
 * 折叠时前 20 行预览，展开全量。
 */
import { Container, Text, type Component } from '@earendil-works/pi-tui';

import { detailsOf, extractText, type RendererInput } from 'nova-tui';

const PREVIEW_LINES = 20;

export default function renderLs(input: RendererInput): Component {
  const d = detailsOf(input);
  const colors = input.env?.colors;
  const dim = (s: string) => colors?.dim?.(s) ?? s;
  const warn = (s: string) => colors?.warning?.(s) ?? s;
  const err = (s: string) => colors?.error?.(s) ?? s;
  const expanded = input.env?.expanded === true;

  const container = new Container();
  if (typeof d.error === 'string' && d.error) {
    container.addChild(new Text(err(`列目录失败：${d.error}`), 1, 0));
    return container;
  }

  const text = extractText(input.result?.content) || extractText(input.partial?.content);
  if (!text) return container;

  const lines = text.split('\n').filter((line) => line.length > 0);
  if (expanded || lines.length <= PREVIEW_LINES) {
    container.addChild(new Text(lines.join('\n'), 1, 0));
  } else {
    container.addChild(new Text(lines.slice(0, PREVIEW_LINES).join('\n'), 1, 0));
    container.addChild(
      new Text(dim(`... (${lines.length - PREVIEW_LINES} more, ctrl+o to expand)`), 1, 0),
    );
  }
  if (d.truncated === true) {
    container.addChild(new Text(warn('条目过多已截断——可提高 limit 重试'), 1, 0));
  }
  return container;
}
