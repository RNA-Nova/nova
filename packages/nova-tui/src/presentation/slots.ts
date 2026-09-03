/**
 * SlotRegistry：统一的内容贡献抽象（设计 v3 §4）。
 *
 * 一切"某处需要一块 UI 内容"都是 slot。四族内建键：
 * - ``tool:<tool_name>``  工具渲染（producer = NovaRenderer）；
 * - ``entry:<role>``      消息/条目渲染；
 * - ``region:<区域>``     区域部件（footer/header/status/widget）；
 * - ``block:<kind>``      块适配器（声明式块 → 前端组件；宿主注册 builtin，
 *   包可注册自定义块类型或覆盖官方适配器——碰撞诊断在案）。
 *
 * 纪律：
 * - 注册表只有一个；内建渲染器与第三方扩展**走同一 register 调用**
 *   （dogfood：没有"内建专用通道"）；
 * - 后注册覆盖同键，来源记录在案（诊断用）；
 * - **通用回退是空态**（resolve 返回 undefined，前端自画 args+文本+状态色），
 *   不是"内建渲染器"。
 */

import type { BlockValidator, NovaRenderer, PreviewComputer } from './blocks.js';

/** slot 生产者：输入呈现模型片段，产出声明式内容（块列表或区域状态）。 */
export type SlotProducer<I = unknown, O = unknown> = (input: I) => O;

/** 一条注册记录（诊断/列举用）。 */
export interface SlotRegistration {
  key: string;
  /** 注册来源（包名 / ``builtin``），覆盖裁决的诊断依据。 */
  source: string;
}

/** 工具渲染 slot 键。 */
export function toolSlot(toolName: string): string {
  return `tool:${toolName}`;
}

/** 消息/条目渲染 slot 键。 */
export function entrySlot(role: string): string {
  return `entry:${role}`;
}

/** 区域部件 slot 键。 */
export function regionSlot(region: string): string {
  return `region:${region}`;
}

/** 块适配器 slot 键。 */
export function blockSlot(kind: string): string {
  return `block:${kind}`;
}

/** 扩展编辑器 slot 键（单例——后注册覆盖，碰撞诊断在案）。 */
export function editorSlot(): string {
  return 'editor:main';
}

/** Node 扩展命令 slot 键（统一命令表的 Node 源——行为注册的呈现侧出口）。 */
export function commandSlot(name: string): string {
  return `command:${name}`;
}

/** 扩展快捷键 slot 键（键名为归一化小写 KeyId——ctrl+shift+k 等）。 */
export function shortcutSlot(key: string): string {
  return `shortcut:${key.toLowerCase()}`;
}

/** 自动补全 provider slot 键（编辑器补全的扩展源——组合进基线补全）。 */
export function autocompleteSlot(name: string): string {
  return `autocomplete:${name}`;
}

/**
 * 自定义对话框 slot 键（``dialog:<name>``——后端经 ``ui.request("dialog:<name>")``
 * 调起的包侧对话框组件；键即线上 componentType，不做二次映射）。
 */
export function dialogSlot(name: string): string {
  return `dialog:${name}`;
}

export class SlotRegistry {
  private readonly producers = new Map<string, { producer: SlotProducer; source: string }>();

  /**
   * 注册生产者（后注册覆盖同键）。返回注销函数。
   * ``source`` 记录注册来源（包名或 ``builtin``），供覆盖诊断。
   * 碰撞检测不在本层——注册表保持纯粹（覆盖即事实），碰撞的生成
   * 归 resources/loader.ts（注册时查 sourceOf 产出诊断）。
   */
  register<I, O>(key: string, producer: SlotProducer<I, O>, source = 'unknown'): () => void {
    const record = { producer: producer as SlotProducer, source };
    this.producers.set(key, record);
    return () => {
      // 只注销自己——若已被后注册者覆盖，不动别人的记录
      if (this.producers.get(key) === record) this.producers.delete(key);
    };
  }

  /** 解析生产者（空态 = undefined，前端走通用回退）。 */
  resolve<I, O>(key: string): SlotProducer<I, O> | undefined {
    return this.producers.get(key)?.producer as SlotProducer<I, O> | undefined;
  }

  /** 注册来源（诊断）。 */
  sourceOf(key: string): string | undefined {
    return this.producers.get(key)?.source;
  }

  /** 全部注册记录（调试面板/诊断用）。 */
  list(): SlotRegistration[] {
    return [...this.producers.entries()].map(([key, record]) => ({
      key,
      source: record.source,
    }));
  }

  /** 工具渲染器的类型化快捷解析。 */
  resolveToolRenderer(toolName: string): NovaRenderer | undefined {
    return this.resolve(toolSlot(toolName));
  }

  // ------------------------------------------------------------------
  // 执行前预览钩子（render 之外的第二条通道：异步只读计算）
  // ------------------------------------------------------------------

  private readonly previews = new Map<string, { compute: PreviewComputer; source: string }>();

  /** 注册工具的预览计算器（渲染器模块的 preview 命名导出）。 */
  registerToolPreview(toolName: string, compute: PreviewComputer, source = 'unknown'): void {
    this.previews.set(toolName, { compute, source });
  }

  /** 解析工具的预览计算器（无则 undefined——该工具无执行前预览）。 */
  resolveToolPreview(toolName: string): PreviewComputer | undefined {
    return this.previews.get(toolName)?.compute;
  }

  // ------------------------------------------------------------------
  // 自定义块校验器（registerBlock 的可选 schema 钩子——与适配器分储：
  // 适配器在 producers 主表走 block:<kind> 键，校验器在此旁路）
  // ------------------------------------------------------------------

  private readonly blockValidators = new Map<
    string,
    { validate: BlockValidator; source: string }
  >();

  /** 注册自定义块的校验器（消费层适配前调用，非空 issues 渲染为错误块）。 */
  registerBlockValidator(kind: string, validate: BlockValidator, source = 'unknown'): void {
    this.blockValidators.set(kind, { validate, source });
  }

  /** 解析自定义块的校验器（无则 undefined——该 kind 无 schema 守护）。 */
  resolveBlockValidator(kind: string): BlockValidator | undefined {
    return this.blockValidators.get(kind)?.validate;
  }
}
