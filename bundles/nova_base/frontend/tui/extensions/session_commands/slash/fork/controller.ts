/**
 * /fork 的包侧编排（—dogfood：官方 bundle 以扩展机制自持命令 UI）。
 *
 * 从用户消息分叉：列出本会话全部 user 消息（最新在前，初始选中即最新），
 * enter → fork RPC（position=before：分支点取
 * 消息父级，原文经 selectedText 回填编辑器——"编辑后重发"体验）；esc 取消。
 * 带参数（/fork <entry_id> [at|before|after]）直调 fork RPC——与后端 _fork
 * 等价（命令分发后参数不再回落后端，包侧自理；缺省 position=after 与后端对齐）。
 * 选择器本体复用宿主通用件 SearchableSelector（'nova-tui/modes/tui/*' 子路径共享）。
 * 后端 /fork 命令保留作 headless 回退。
 */

import type { ExtensionUIContext } from 'nova-tui';

import {
  SearchableSelector,
  type SearchableItem,
} from 'nova-tui/modes/tui/components/pickers/searchable';

/** 线上条目（getSessionEntries 的自由负载——这里只消费分叉需要的字段）。 */
interface RawEntry {
  id?: unknown;
  type?: unknown;
  message?: { role?: unknown; content?: unknown } | undefined;
}

/** 提取 user 消息文本（content 为 string 或块数组；首行压缩为空格归一）。 */
export function extractUserText(content: unknown): string {
  let text = '';
  if (typeof content === 'string') {
    text = content;
  } else if (Array.isArray(content)) {
    text = content
      .filter(
        (b): b is { type: string; text: string } =>
          typeof b === 'object' && b !== null && (b as { type?: unknown }).type === 'text',
      )
      .map((b) => b.text)
      .join(' ');
  }
  return text.replace(/\s+/g, ' ').trim();
}

/** 选择器条目组装（纯函数——时间序收集 → 最新在前；description 为倒数位次）。 */
export function buildForkItems(entries: RawEntry[]): SearchableItem[] {
  return entries
    .filter(
      (entry) =>
        entry?.type === 'message' &&
        entry.message?.role === 'user' &&
        typeof entry.id === 'string',
    )
    .map((entry) => ({
      value: entry.id as string,
      label: extractUserText(entry.message?.content) || '(空消息)',
    }))
    .reverse()
    .map((item, index, all) => ({
      ...item,
      description: `消息 ${all.length - index}/${all.length}`,
    }));
}

/** fork RPC + 原文回填编辑器（selectedText 回填——编辑后重发）。 */
async function forkAt(
  ctx: ExtensionUIContext,
  entryId: string,
  position: 'at' | 'before' | 'after',
): Promise<void> {
  try {
    const result = (await ctx.invoke('fork', { entryId, position })) as {
      selectedText?: string | null;
      cancelled?: boolean;
    };
    if (result?.cancelled) return;
    if (result?.selectedText) {
      ctx.setEditorText?.(result.selectedText);
    }
    ctx.notify('已分叉到新会话', 'info');
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
  }
}

/** /fork 命令入口：带参数直调（/fork <entry_id> [at|before|after]），无参开选择器。 */
export async function handleForkCommand(args: string, ctx: ExtensionUIContext): Promise<void> {
  const [entryId, position = 'after'] = args.trim().split(/\s+/).filter(Boolean);
  if (!entryId) {
    await openForkSelector(ctx);
    return;
  }
  if (position !== 'at' && position !== 'before' && position !== 'after') {
    ctx.notify('position 必须是 at、before 或 after', 'error');
    return;
  }
  await forkAt(ctx, entryId, position);
}

/** 打开分叉选择器（/fork 无参与双 Esc 共用入口——双 Esc 由宿主推 '/fork' 命令）。 */
export async function openForkSelector(ctx: ExtensionUIContext): Promise<void> {
  if (!ctx.custom) {
    ctx.notify('当前前端不支持模态对话框（custom 原语未注入）', 'warning');
    return;
  }
  let entries: RawEntry[];
  try {
    const result = await ctx.invoke('getSessionEntries', {});
    entries = ((result as { entries?: RawEntry[] }).entries ?? []) as RawEntry[];
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
    return;
  }
  const items = buildForkItems(entries);
  if (items.length === 0) {
    ctx.notify('没有可分叉的用户消息', 'info');
    return;
  }

  const selected = await ctx.custom<string | undefined>((_env, done) => {
    return new SearchableSelector('从消息分叉', items, {
      onSelect: (entryId) => done(entryId),
      onCancel: () => done(undefined),
    });
  });
  if (selected === undefined || selected === null) return;
  await forkAt(ctx, selected, 'before');
}
