/**
 * /todos 的包侧编排（dogfood：官方 bundle 以扩展机制自持命令 UI）。
 *
 * 数据：`getSessionEntries` 全量条目 + `getSessionState.leafId`——从 leaf
 * 沿 parentId 回溯出**当前分支**（树导航后所见即该历史点的清单快照，
 * 与后端 _latest_todo_list 的 get_branch 语义一致），取最新一条 todo
 * 工具结果的 details.todos。后端 /todos 命令保留作 headless 回退。
 */

import type { ExtensionUIContext } from 'nova-tui';

import { TodosViewer, type TodoViewItem } from './viewer.js';

/** 会话条目的最小线形（只取本编排消费的字段）。 */
interface SessionEntryLike {
  id?: unknown;
  parentId?: unknown;
  type?: unknown;
  message?: {
    role?: unknown;
    toolName?: unknown;
    details?: { todos?: unknown } | null;
  } | null;
}

/** 从 leaf 回溯当前分支，取最新一条 todo 清单（无则 undefined——从未有过）。 */
export function latestTodosFromEntries(
  entries: SessionEntryLike[],
  leafId: string | null,
): TodoViewItem[] | undefined {
  const byId = new Map<string, SessionEntryLike>();
  for (const entry of entries) {
    if (typeof entry?.id === 'string') byId.set(entry.id, entry);
  }

  // leaf → root 回溯即分支逆序；沿途首个 todo 结果就是最新清单。
  let cursor = leafId ? byId.get(leafId) : undefined;
  while (cursor) {
    if (cursor.type === 'message') {
      const message = cursor.message;
      if (message?.role === 'toolResult' && message.toolName === 'todo') {
        const todos = message.details?.todos;
        if (Array.isArray(todos)) {
          return todos.map((t) => ({
            content: typeof t?.content === 'string' ? t.content : '',
            status: typeof t?.status === 'string' ? t.status : 'pending',
          }));
        }
        return undefined;
      }
    }
    const parentId = cursor.parentId;
    cursor = typeof parentId === 'string' ? byId.get(parentId) : undefined;
  }
  return undefined;
}

/** 打开 /todos 模态查看器（/todos 命令入口）。 */
export async function openTodosViewer(ctx: ExtensionUIContext): Promise<void> {
  if (!ctx.custom) {
    ctx.notify('当前前端不支持模态对话框（custom 原语未注入）', 'warning');
    return;
  }
  let todos: TodoViewItem[] | undefined;
  try {
    const [entriesResult, snapshot] = await Promise.all([
      ctx.invoke('getSessionEntries', {}),
      ctx.invoke('getSessionState', {}),
    ]);
    const entries = ((entriesResult as { entries?: SessionEntryLike[] }).entries ?? []).filter(
      (entry) => typeof entry?.id === 'string',
    );
    const leafId = (snapshot as { leafId?: string | null }).leafId ?? null;
    todos = latestTodosFromEntries(entries, leafId);
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
    return;
  }
  if (todos === undefined) {
    ctx.notify('当前分支还没有 todo 清单（让 agent 用 todo 工具创建）', 'info');
    return;
  }
  await ctx.custom<void>((_env, done) => new TodosViewer(todos, () => done()));
}
