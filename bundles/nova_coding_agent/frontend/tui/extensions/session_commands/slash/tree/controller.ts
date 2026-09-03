/**
 * /tree 的包侧编排（dogfood：官方 bundle 以扩展机制自持命令 UI）。
 *
 * 全部交互经 ExtensionUIContext：custom 模态挂载树选择器、select/input 走
 * 摘要问询、invoke 直达后端 RPC、invokeCancellable + registerForegroundTask
 * 承载摘要生成的 Esc 取消、setEditorText 回填（navigate 的 editorText）。
 * 后端 /tree 命令保留作 headless 回退（bundle 缺席/无 UI 时）。
 */

import type { ExtensionUIContext } from 'nova-tui';

import {
  TreeSelector,
  deriveLabels,
  deriveLabelTimestamps,
  entryCopyText,
  type TreeEntry,
} from './selector.js';
import {
  getTreeFilterMode,
  isBranchSummarySkipPrompt,
} from 'nova-tui/modes/tui/utils/tui-settings';

/** /tree 命令分发：带 id 直接跳转（与后端 headless 回退同语义），无参数开选择器。 */
export async function handleTreeCommand(args: string, ctx: ExtensionUIContext): Promise<void> {
  const targetId = args.trim();
  if (targetId) {
    try {
      const result = (await ctx.invoke('navigateTree', {
        entryId: targetId,
      })) as { editorText?: string; cancelled?: boolean };
      if (result?.cancelled) return;
      if (typeof result?.editorText === 'string' && result.editorText) {
        ctx.setEditorText?.(result.editorText);
      }
    } catch (error) {
      ctx.notify(error instanceof Error ? error.message : String(error), 'error');
    }
    return;
  }
  await openTreeSelector(ctx);
}

/** 打开树选择器（/tree 命令入口；取消/中止后重开走递归重入）。 */
export async function openTreeSelector(ctx: ExtensionUIContext): Promise<void> {
  if (!ctx.custom) {
    ctx.notify('当前前端不支持模态对话框（custom 原语未注入）', 'warning');
    return;
  }
  let entries: TreeEntry[];
  let leafId: string | null;
  try {
    const [entriesResult, snapshot] = await Promise.all([
      ctx.invoke('getSessionEntries', {}),
      ctx.invoke('getSessionState', {}),
    ]);
    entries = ((entriesResult as { entries?: TreeEntry[] }).entries ?? []).filter(
      (entry) => typeof entry?.id === 'string',
    );
    leafId = (snapshot as { leafId?: string | null }).leafId ?? null;
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
    return;
  }
  if (entries.length === 0) {
    ctx.notify('空会话，无树可显示', 'info');
    return;
  }

  const selected = await ctx.custom<string | undefined>((_env, done) => {
    return new TreeSelector(
      entries,
      leafId,
      deriveLabels(entries),
      {
        onSelect: (entryId) => done(entryId),
        onCancel: () => done(undefined),
        onLabelEdit: (entryId, label) => {
          ctx
            .invoke('setLabel', { entryId, label: label ?? null })
            .catch((error: unknown) =>
              ctx.notify(error instanceof Error ? error.message : String(error), 'error'),
            );
        },
        onCopy: (entryId) => void copyEntry(ctx, entries, entryId),
      },
      deriveLabelTimestamps(entries),
      getTreeFilterMode(),
    );
  });
  if (selected === undefined || selected === null) return;
  await handleNavigate(ctx, selected, leafId);
}

/** ctrl+x 复制选中条目全文。 */
async function copyEntry(
  ctx: ExtensionUIContext,
  entries: TreeEntry[],
  entryId: string,
): Promise<void> {
  const entry = entries.find((candidate) => candidate.id === entryId);
  if (!entry) return;
  const text = entryCopyText(entry);
  if (!text) {
    ctx.notify('该条目没有可复制的文本', 'info');
    return;
  }
  const ok = (await ctx.writeClipboard?.(text)) ?? false;
  ctx.notify(ok ? '已复制条目内容' : '复制失败（无可用剪贴板通道）', 'info');
}

/** enter 跳转：摘要问询（设置档可跳过）→ navigateTree → editorText 回填。 */
async function handleNavigate(
  ctx: ExtensionUIContext,
  targetId: string,
  leafId: string | null,
): Promise<void> {
  if (targetId === leafId) {
    ctx.notify('已在当前节点', 'info');
    return;
  }
  const options: Record<string, unknown> = {};
  // 设置档跳过摘要问询（pi branchSummarySkipPrompt 对位）
  if (!isBranchSummarySkipPrompt()) {
    const choice = await ctx.select?.('生成分支摘要？', [
      { value: 'none', label: '不生成摘要', description: '直接跳转' },
      { value: 'summarize', label: '生成摘要', description: '分支内容摘要进新上下文' },
      { value: 'custom', label: '自定义摘要指令', description: '输入自定义 prompt' },
    ]);
    if (choice === undefined) {
      await openTreeSelector(ctx); // Esc：重开树（pi 同款回退）
      return;
    }
    if (choice === 'summarize') {
      options.summarize = true;
    } else if (choice === 'custom') {
      const instructions = await ctx.input?.('自定义摘要指令');
      if (instructions === undefined || instructions.trim() === '') {
        await openTreeSelector(ctx);
        return;
      }
      options.summarize = true;
      options.customInstructions = instructions;
    }
  }

  try {
    const summarizing = options.summarize === true;
    let result: { editorText?: string | null; cancelled?: boolean };
    if (summarizing && ctx.invokeCancellable) {
      // 摘要生成是 LLM 调用（秒级）——可取消调用 + 前台任务登记（Esc 取消）
      ctx.notify('正在生成分支摘要…（esc 取消）', 'info');
      const call = ctx.invokeCancellable('navigateTree', { targetId, options });
      const unregister = ctx.registerForegroundTask?.(() => {
        call.cancel();
        void ctx.invoke('abortBranchSummary', {}).catch(() => undefined);
        ctx.notify('已取消分支摘要', 'info');
      });
      try {
        result = (await call.promise) as typeof result;
      } catch (error) {
        if (error instanceof Error && error.name === 'AbortError') {
          await openTreeSelector(ctx); // Esc 中止：重开树（pi aborted 语义）
          return;
        }
        throw error;
      } finally {
        unregister?.();
      }
    } else {
      result = (await ctx.invoke('navigateTree', { targetId, options })) as typeof result;
    }
    if (result?.cancelled) {
      await openTreeSelector(ctx); // 扩展取消/摘要中止：重开树
      return;
    }
    // 目标是 user 消息时原文回填编辑器（仅当编辑器为空——不覆盖草稿）
    if (result?.editorText && (ctx.getEditorText?.() ?? '').trim() === '') {
      ctx.setEditorText?.(result.editorText);
    }
    ctx.notify('已跳转到所选节点', 'info');
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
  }
}
