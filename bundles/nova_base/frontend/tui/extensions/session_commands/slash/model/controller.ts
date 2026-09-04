/**
 * /model 的包侧编排（—dogfood：官方 bundle 以扩展机制自持命令 UI）。
 *
 * 当前模型置顶带 ✓、Tab 切 all/scoped 作用域、fuzzy 搜索、provider 分组；
 * 选中即 setModel。带参数（/model provider/id）时直切——与后端 _model 的
 * set_model 等价（命令分发后参数不再回落后端，包侧自理）。
 * 选择器本体复用宿主通用件 SearchableSelector（'nova-tui/modes/tui/*' 子路径
 * 共享——jiti 别名 + ESM 缓存，主题/键位单例与宿主同实例）。
 * 后端 /model 命令保留作 headless 回退（bundle 缺席/无 UI 时）。
 */

import type { ExtensionUIContext } from 'nova-tui';

import {
  SearchableSelector,
  type SearchableItem,
} from 'nova-tui/modes/tui/components/pickers/searchable';

/** 线上模型条目（listModels 的消费面）。 */
export interface ModelListItem {
  provider: string;
  id: string;
  name: string;
  available: boolean;
  reasoning: boolean;
}

/** 线上 scoped 池条目（listScopedModels 的消费面）。 */
export interface ScopedModelItem {
  provider: string;
  id: string;
  thinkingLevel: string | null;
}

/** 模型引用键（`provider/id`——setModel 的 model 参数形态）。 */
export function modelKey(model: { provider: string; id: string }): string {
  return `${model.provider}/${model.id}`;
}

/**
 * 选择器条目组装（纯函数）：scoped 档过滤池 → 当前模型置顶（* 其余保持后端顺序）→ ✓ 前缀 + 元信息描述列 + provider 分组。
 */
export function buildModelItems(
  models: ModelListItem[],
  current: { provider: string; id: string } | null,
  scoped: ScopedModelItem[],
  scope: 'all' | 'scoped',
): SearchableItem[] {
  const pool =
    scope === 'scoped'
      ? models.filter((m) => scoped.some((s) => s.provider === m.provider && s.id === m.id))
      : models;
  const isCurrent = (m: ModelListItem): number =>
    current !== null && m.provider === current.provider && m.id === current.id ? 1 : 0;
  return [...pool]
    .sort((a, b) => isCurrent(b) - isCurrent(a))
    .map((m) => {
      const meta = [m.name, m.reasoning ? 'reasoning' : '', m.available ? '' : '未配置凭据']
        .filter(Boolean)
        .join(' · ');
      return {
        value: modelKey(m),
        label: `${isCurrent(m) ? '✓ ' : ''}${modelKey(m)}`,
        description: meta,
        group: m.provider,
      };
    });
}

/** setModel + 提示（选择器选中与带参数直切共用）。 */
async function setModel(ctx: ExtensionUIContext, ref: string): Promise<void> {
  try {
    const result = (await ctx.invoke('setModel', { model: ref })) as { ok?: boolean };
    // 后端在缺凭据时返回 ok:false（不抛错）——必须显式判读，
    // 否则"已切换"是谎言（footer 没变、消息说切了）
    if (result && result.ok === false) {
      ctx.notify(`无法切换到 ${ref}：该模型未配置凭据（/login 配置后重试）`, 'error');
      return;
    }
    ctx.notify(`已切换模型: ${ref}`, 'info');
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
  }
}

/** /model 命令入口：带参数直切（/model provider/id），无参开选择器。 */
export async function handleModelCommand(args: string, ctx: ExtensionUIContext): Promise<void> {
  const ref = args.trim();
  if (ref) {
    await setModel(ctx, ref);
    return;
  }
  await openModelSelector(ctx);
}

/** 打开模型选择器（/model 无参与 ctrl+l 共用入口——ctrl+l 由宿主推 '/model' 命令）。 */
export async function openModelSelector(ctx: ExtensionUIContext): Promise<void> {
  if (!ctx.custom) {
    ctx.notify('当前前端不支持模态对话框（custom 原语未注入）', 'warning');
    return;
  }
  let models: ModelListItem[];
  let current: { provider: string; id: string } | null;
  let scoped: ScopedModelItem[];
  try {
    const [listResult, snapshot, scopedResult] = await Promise.all([
      ctx.invoke('listModels', {}),
      ctx.invoke('getSessionState', {}),
      ctx.invoke('listScopedModels', {}),
    ]);
    models = ((listResult as { models?: ModelListItem[] }).models ?? []) as ModelListItem[];
    current = (snapshot as { model?: { provider: string; id: string } | null }).model ?? null;
    scoped = ((scopedResult as { models?: ScopedModelItem[] }).models ?? []) as ScopedModelItem[];
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
    return;
  }
  if (models.length === 0) {
    ctx.notify('没有可用模型', 'info');
    return;
  }

  // 作用域状态：all（全部）⇄ scoped（仅 scoped 池）——Tab 切换
  let scope: 'all' | 'scoped' = scoped.length > 0 ? 'scoped' : 'all';
  const scopeTitle = (s: 'all' | 'scoped') =>
    `选择模型（作用域: ${s} · Tab 切 ${s === 'all' ? 'scoped' : 'all'}）`;
  const selected = await ctx.custom<string | undefined>((_env, done) => {
    const selector = new SearchableSelector(
      scopeTitle(scope),
      buildModelItems(models, current, scoped, scope),
      {
        onSelect: (value) => done(value),
        onCancel: () => done(undefined),
        onTab: () => {
          // 空池守卫：scoped 池为空时不切（切过去只会看到"无匹配"——零信息）
          if (scoped.length === 0) {
            ctx.notify('Scoped 池为空——/scoped-models 启用模型后可按池筛选', 'info');
            return;
          }
          scope = scope === 'all' ? 'scoped' : 'all';
          selector.setTitle(scopeTitle(scope));
          selector.setItems(buildModelItems(models, current, scoped, scope));
        },
      },
    );
    return selector;
  });
  if (selected === undefined || selected === null) return;
  await setModel(ctx, selected);
}
