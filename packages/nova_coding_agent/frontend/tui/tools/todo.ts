/**
 * todo 工具渲染器（组件形态——清单语义对齐 Claude Code TodoWrite 的展示习惯）。
 *
 * details 契约（backend/tools/todo.py）：
 *   成功 { todos: [{ content, status }] }（status: pending|in_progress|completed）；
 *   失败 { error }。
 *
 * 呈现语义：
 * - 折叠态：进度行（n/m completed）+ 进行中断言 + 前 5 条 + 余量提示；
 * - 展开态（ctrl+o）：全量清单；
 * - 图标 ○ pending / ◐ in_progress（accent 高亮）/ ✓ completed（dim 淡出）。
 */
import { Container, Text, type Component } from '@earendil-works/pi-tui';

import { detailsOf, type RendererInput } from 'nova-client';

/** 折叠态展示的条目数（pi todo 渲染同款）。 */
const COLLAPSED_COUNT = 5;

interface TodoItem {
  content?: string;
  status?: string;
}

export default function renderTodo(input: RendererInput): Component {
  const d = detailsOf(input);
  const colors = input.env?.colors;
  const expanded = input.env?.expanded === true;
  const dim = (s: string) => colors?.dim?.(s) ?? s;
  const accent = (s: string) => colors?.accent?.(s) ?? s;
  const ok = (s: string) => colors?.success?.(s) ?? s;
  const bad = (s: string) => colors?.error?.(s) ?? s;
  const muted = (s: string) => colors?.muted?.(s) ?? s;

  const container = new Container();

  if (typeof d.error === 'string' && d.error) {
    container.addChild(new Text(bad(`Error: ${d.error}`), 1, 0));
    return container;
  }

  const todos = Array.isArray(d.todos) ? (d.todos as TodoItem[]) : [];
  if (todos.length === 0) {
    container.addChild(
      new Text(input.status === 'done' ? dim('(empty list)') : dim('running…'), 1, 0),
    );
    return container;
  }

  const completed = todos.filter((t) => t.status === 'completed').length;
  const inProgress = todos.filter((t) => t.status === 'in_progress').length;
  let progress = muted(`${completed}/${todos.length} completed`);
  if (inProgress > 0) progress += dim(` · ${inProgress} in progress`);
  container.addChild(new Text(progress, 1, 0));

  const toShow = expanded ? todos : todos.slice(0, COLLAPSED_COUNT);
  for (const t of toShow) {
    const content = t.content ?? '';
    if (t.status === 'completed') {
      container.addChild(new Text(`${ok('✓')} ${dim(content)}`, 1, 0));
    } else if (t.status === 'in_progress') {
      container.addChild(new Text(`${accent('◐')} ${accent(content)}`, 1, 0));
    } else {
      container.addChild(new Text(`${dim('○')} ${muted(content)}`, 1, 0));
    }
  }
  if (!expanded && todos.length > COLLAPSED_COUNT) {
    container.addChild(
      new Text(dim(`... ${todos.length - COLLAPSED_COUNT} more (ctrl+o to expand)`), 1, 0),
    );
  }
  return container;
}
