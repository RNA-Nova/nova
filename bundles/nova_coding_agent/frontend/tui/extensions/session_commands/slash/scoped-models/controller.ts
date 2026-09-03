/**
 * /scoped-models 的包侧编排（pi scoped-models-selector 对位——dogfood：官方 bundle 以扩展机制自持命令 UI）。
 *
 * scoped 池 = ctrl+p 循环模型的启用集与循环顺序（session 级配置）。
 * 面板交互（启用/排序/全启/全清——ctrl+s 才写 setScopedModels，pi "session-only
 * until saved" 语义）在 selector.ts（从 nova-client 宿主迁入）。
 * 后端 /scoped-models 命令保留作 headless 回退（文本清单）。
 */

import type { ExtensionUIContext } from 'nova-tui';

import { ScopedModelsSelector } from './selector.js';

/** 线上模型条目（listModels 的消费面）。 */
interface ModelListItem {
  provider: string;
  id: string;
  name: string;
}

/** 线上 scoped 池条目（listScopedModels 的消费面）。 */
interface ScopedModelItem {
  provider: string;
  id: string;
  thinkingLevel: string | null;
}

/** 打开 scoped 模型池面板（/scoped-models 命令入口）。 */
export async function openScopedModelsPanel(ctx: ExtensionUIContext): Promise<void> {
  if (!ctx.custom) {
    ctx.notify('当前前端不支持模态对话框（custom 原语未注入）', 'warning');
    return;
  }
  let models: ModelListItem[];
  let scoped: ScopedModelItem[];
  try {
    const [listResult, scopedResult] = await Promise.all([
      ctx.invoke('listModels', {}),
      ctx.invoke('listScopedModels', {}),
    ]);
    models = ((listResult as { models?: ModelListItem[] }).models ?? []) as ModelListItem[];
    scoped = ((scopedResult as { models?: ScopedModelItem[] }).models ?? []) as ScopedModelItem[];
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
    return;
  }

  const saved = await ctx.custom<string[] | undefined>((_env, done) => {
    return new ScopedModelsSelector(scoped, models, {
      onSave: (orderedKeys) => done(orderedKeys),
      onCancel: () => done(undefined),
    });
  });
  if (saved === undefined || saved === null) return;

  // 保存：thinkingLevel 跟随原 scoped 条目（新启用的为 null）；
  // id 取键的剩余段（模型 id 本身可含 '/'）
  const inputs = saved.map((key) => {
    const [provider, ...rest] = key.split('/');
    const existing = scoped.find((s) => s.provider === provider && s.id === rest.join('/'));
    return {
      provider,
      modelId: null,
      id: rest.join('/'),
      thinkingLevel: existing?.thinkingLevel ?? null,
    };
  });
  try {
    await ctx.invoke('setScopedModels', { models: inputs });
    ctx.notify(`Scoped 模型池已保存（${inputs.length} 个——ctrl+p 循环）`, 'info');
  } catch (error) {
    ctx.notify(error instanceof Error ? error.message : String(error), 'error');
  }
}
