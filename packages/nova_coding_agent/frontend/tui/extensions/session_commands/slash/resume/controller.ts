/**
 * /resume 的包侧编排（pi showSessionSelector 对位——dogfood：官方 bundle 以扩展机制自持命令 UI）。
 *
 * 数据：listSessions（富字段 + scope）→ switchSession 切换；
 * 删除 deleteSession / 重命名 renameSession（变更后重载列表——模态保持，
 * 经选择器 setItems 刷新）。选择器本体在 selector.ts（从 nova-client 宿主迁入）。
 * 后端 /resume 命令保留作 headless 回退。
 */

import type { ExtensionUIContext } from 'nova-client';

import { SessionSelector, type SessionItem } from './selector.js';

/** 线上会话行（listSessions 的消费面）。 */
interface SessionListRow {
  id: string;
  /** 后端恒为 string（未知空串——load 归一为 null）。 */
  name: string;
  path: string;
  modified: number;
  messageCount?: number;
  firstMessage?: string;
  cwd?: string;
  parentSessionPath?: string | null;
}

/** 拉取会话列表（排除当前活跃会话——切换目标不含自身）。 */
async function loadSessions(
  ctx: ExtensionUIContext,
  scope: 'current' | 'all',
): Promise<SessionItem[]> {
  const [listResult, snapshot] = await Promise.all([
    ctx.invoke('listSessions', { scope }),
    ctx.invoke('getSessionState', {}),
  ]);
  const currentFile = (snapshot as { sessionFile?: string | null }).sessionFile ?? null;
  const rows = Array.isArray(listResult) ? (listResult as SessionListRow[]) : [];
  return rows
    .filter((row) => typeof row?.path === 'string' && row.path !== currentFile)
    .map((row) => ({
      path: row.path,
      // 后端 name 恒为 string（未知时空串）——空串归一为 null（unnamed 语义）
      name: row.name ? row.name : null,
      firstMessage: row.firstMessage ?? '',
      messageCount: row.messageCount ?? 0,
      modified: row.modified ?? 0,
      cwd: row.cwd ?? '',
      parentSessionPath: row.parentSessionPath ?? null,
    }));
}

/** 作用域切换/变更后的重载（选择器仍在开时刷新条目）。 */
async function reloadSessions(
  ctx: ExtensionUIContext,
  selector: SessionSelector,
  scope: 'current' | 'all',
): Promise<void> {
  try {
    selector.setItems(await loadSessions(ctx, scope));
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function removeSession(
  ctx: ExtensionUIContext,
  selector: SessionSelector,
  path: string,
): Promise<void> {
  try {
    await ctx.invoke('deleteSession', { path });
    ctx.notify('会话已删除', 'info');
  } catch (error) {
    // 当前活跃会话删除被后端拒绝（SESSION_IN_USE）等——报错但不关选择器
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
  }
  await reloadSessions(ctx, selector, selector.currentScope);
}

async function renameSession(
  ctx: ExtensionUIContext,
  selector: SessionSelector,
  path: string,
  name: string,
): Promise<void> {
  try {
    await ctx.invoke('renameSession', { path, name });
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
  }
  await reloadSessions(ctx, selector, selector.currentScope);
}

/** 打开会话选择器（/resume 命令入口）。 */
export async function openResumeSelector(ctx: ExtensionUIContext): Promise<void> {
  if (!ctx.custom) {
    ctx.notify('当前前端不支持模态对话框（custom 原语未注入）', 'warning');
    return;
  }
  let items: SessionItem[];
  try {
    items = await loadSessions(ctx, 'current');
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
    return;
  }
  if (items.length === 0) {
    ctx.notify('当前目录没有历史会话', 'info');
    return;
  }

  const selected = await ctx.custom<string | undefined>((_env, done) => {
    const selector = new SessionSelector(items, {
      onSelect: (path) => done(path),
      onCancel: () => done(undefined),
      onDelete: (path) => void removeSession(ctx, selector, path),
      onRename: (path, name) => void renameSession(ctx, selector, path, name),
      onScopeChange: (scope) => void reloadSessions(ctx, selector, scope),
    });
    return selector;
  });
  if (selected === undefined || selected === null) return;
  try {
    await ctx.invoke('switchSession', { path: selected });
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
  }
}
