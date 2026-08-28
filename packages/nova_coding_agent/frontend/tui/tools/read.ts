/**
 * read 工具渲染器（文件阅读风，组件形态——pi read.ts 渲染语义对位）。
 *
 * details 契约（backend/tools/read.py）：
 *   文本：path / truncated / truncated_by / total_lines；
 *   图片：path / size / mime / resized；错误：{ error, path? }。
 *
 * 呈现语义（pi 对齐）：**未展开时内容区为空**（卡片只剩宿主标题行），
 * 展开时全量高亮（代码 fence + env 的 Markdown 主题）+ 截断提示。
 */
import { Container, Markdown, Text, type Component } from '@earendil-works/pi-tui';

import { detailsOf, extractText, type RendererInput, type RendererResultPart } from 'nova-client';
import { renderTableLines } from 'nova-client/modes/tui/blocks/table';

export default function renderRead(input: RendererInput): Component {
  const d = detailsOf(input);
  const colors = input.env?.colors;
  const dim = (s: string) => colors?.dim?.(s) ?? s;
  const err = (s: string) => colors?.error?.(s) ?? s;
  const expanded = input.env?.expanded === true;

  const container = new Container();

  if (typeof d.error === 'string' && d.error) {
    container.addChild(new Text(err(`读取失败：${d.error}`), 1, 0));
    return container;
  }

  // pi 语义：未展开时内容区为空（截断提示仍显示——可操作提示要在）
  if (!expanded) {
    if (d.truncated === true) {
      const total = typeof d.total_lines === 'number' ? ` of ${d.total_lines}` : '';
      container.addChild(new Text(dim(`（内容已折叠——ctrl+o 展开查看；共${total} 行）`), 1, 0));
    }
    return container;
  }

  const path = typeof d.path === 'string' ? d.path : undefined;
  // item.result 线上为 unknown——按结果片段形状窄化后取文本
  const result = input.item.result as RendererResultPart | undefined;
  const text = extractText(result?.content);
  if (text) {
    const fenced = `\`\`\`\n${text}\n\`\`\``;
    container.addChild(new Markdown(fenced, 1, 0, input.env?.markdownTheme as never));
    if (path) container.addChild(new Text(dim(path), 1, 0));
  }

  const meta: string[][] = [];
  if (typeof d.total_lines === 'number') meta.push(['total_lines', String(d.total_lines)]);
  if (d.truncated === true) {
    const by = typeof d.truncated_by === 'string' ? d.truncated_by : 'true';
    meta.push(['truncated', by === 'bytes' ? '50KB limit' : by]);
  }
  if (typeof d.mime === 'string') meta.push(['mime', d.mime]);
  if (d.resized === true) meta.push(['image', 'resized']);
  if (meta.length > 0) {
    container.addChild(new Text(renderTableLines(['field', 'value'], meta), 1, 0));
  }

  return container;
}
