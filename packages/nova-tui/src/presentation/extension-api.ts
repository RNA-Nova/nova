/**
 * 扩展 UI API（``ui/index.ts`` 工厂的入参契约，设计 §6）。
 *
 * 复合包的 ``ui/index.ts`` 默认导出工厂函数：``(api) => void``——
 * 编程式注册入口（与 ``ui/renderers/*.ts`` 文件约定并存：文件约定零样板，
 * 工厂可条件注册/共享 helper/注册非渲染器贡献）。
 *
 * 边界：行为注册（命令/工具/事件）不经此 API——归后端 Python 扩展；
 * 本 API 只管**呈现贡献**（渲染器/区域部件；自定义块与扩展编辑器归 P2-3）。
 */

import type {
  BlockValidator,
  NovaBlock,
  NovaRenderer,
  PreviewComputer,
} from './blocks.js';
import {
  autocompleteSlot,
  blockSlot,
  commandSlot,
  dialogSlot,
  editorSlot,
  entrySlot,
  regionSlot,
  shortcutSlot,
  toolSlot,
  type SlotProducer,
  type SlotRegistry,
} from './slots.js';
import type { UISettings, UIStateStore } from '../settings/store.js';

/** 区域部件上下文（v1：cwd——部件自读动态数据，如 git 状态）。 */
export interface RegionContext {
  cwd: string;
}

/** 区域部件生产者：上下文 → 声明式块列表（纯函数，宿主渲染层适配）。 */
export type RegionProducer = (ctx: RegionContext) => NovaBlock[];

/**
 * 自定义块适配器（宿主特定）：块数据 → 宿主组件。
 * 框架无关层只约定"输入块、产出组件"——TUI 宿主下返回 pi-tui
 * ``Component``（消费点按此断言）；Web 宿主（M3+）有自己的适配形态。
 */
export type CustomBlockAdapter = (block: NovaBlock) => unknown;

/** 自定义块定义（registerBlock 的 def）。 */
export interface CustomBlockDef {
  /** 块 → 宿主组件。 */
  adapter: CustomBlockAdapter;
  /** 可选 schema 校验钩子：返回问题清单（空 = 合法），非空渲染为错误块。 */
  validate?: BlockValidator;
}

/**
 * 扩展编辑器工厂（宿主特定）：TUI 宿主下产出 pi-tui ``EditorComponent``
 * 接口的实现（vim 模式等自定义编辑器——装配层热替换默认编辑器）。
 * 入参为宿主编辑器环境（TUI：tui/theme/keybindings）——框架无关层
 * 以 unknown 承载，宿主侧断言。
 */
export type EditorFactory = (env: unknown) => unknown;

/**
 * 区域组件工厂（逃生舱——宿主特定）：TUI 宿主下产出 pi-tui ``Component``。
 * 与声明式 RegionProducer 同键竞争（后注册覆盖、碰撞诊断在案）——
 * 声明式是默认轨（可过网），组件工厂是同进程全自由（有状态/可交互）。
 */
export type RegionComponentFactory = (env: unknown) => unknown;

/**
 * 扩展命令/快捷键的执行上下文（框架无关最小面——宿主注入实现）：
 * ``invoke`` 直达后端 RPC；``notify`` 消息出口（transcript 本地提示）；
 * 对话框与编辑器/终端访问器为**可选**——宿主未注入则为 undefined，
 * 扩展判空降级（全部 UI 向）。
 */
export interface ExtensionUIContext {
  invoke(method: string, params?: Record<string, unknown>): Promise<unknown>;
  /** 可取消调用（长 RPC——分支摘要等；cancel 本地 AbortError + 上行 cancelRequest）。 */
  invokeCancellable?(
    method: string,
    params?: Record<string, unknown>,
  ): { promise: Promise<unknown>; cancel: () => void };
  notify(message: string, level?: 'info' | 'warning' | 'error'): void;
  select?(
    title: string,
    items: Array<{ value: string; label: string; description?: string }>,
  ): Promise<string | undefined>;
  refreshPackages?(): Promise<void>;
  /**
   * 登记前台在飞任务（Esc 域级路由消费——长操作的取消入口）：
   * 注册取消闭包，返回注销函数（任务收尾时调用）。
   */
  registerForegroundTask?(cancel: () => void): () => void;

  // ---- 本地对话框（confirm/input/editor 为便捷形态）----
  /** 确认框。 */
  confirm?(title: string, message: string): Promise<boolean>;
  /** 单行文本框。 */
  input?(title: string, placeholder?: string): Promise<string | undefined>;
  /** 多行编辑器框。 */
  editor?(title: string, prefill?: string): Promise<string | undefined>;
  /**
   * 模态自定义对话框（—**逃生舱核心件**）：
   * 工厂产出宿主组件（TUI：pi-tui Component & Focusable），挂载进编辑器槽位
   * （或 overlay 浮层）；组件经 ``done(result)`` 交还结果并关框；取消语义
   * 归组件自管（Esc 处理是作者职责）。``overlay`` 提供时挂浮层。
   */
  custom?<T>(
    factory: (env: unknown, done: (result?: T) => void) => unknown,
    options?: { overlay?: NovaOverlayOptions },
  ): Promise<T | undefined>;

  // ---- 编辑器通道----
  getEditorText?(): string;
  setEditorText?(text: string): void;
  /** 粘贴到编辑器（大文本折叠等粘贴语义由编辑器决定）。 */
  pasteToEditor?(text: string): void;
  /** 写系统剪贴板（OSC52/平台通道——返回是否成功）。 */
  writeClipboard?(text: string): Promise<boolean>;

  // ---- 状态与终端 ----
  /** 扩展状态行（—footer 扩展位，key 幂等覆盖；undefined 清除）。 */
  setStatus?(key: string, text: string | undefined): void;
  /** 原始终端输入拦截（—返回反注册函数；handler 返回 true 消费）。 */
  onTerminalInput?(handler: (data: string) => boolean | undefined): () => void;
  /** 工具展开态（—ctrl+o 全局开关）。 */
  getToolsExpanded?(): boolean;
  setToolsExpanded?(expanded: boolean): void;

  // ---- 主题访问器----
  getTheme?(): string;
  getAllThemes?(): Array<{ name: string; source: string }>;
  setTheme?(name: string): void;

  // ---- 事件观察口（前端本地事实只读桥——响应式 UI 的最后一块积木）----
  events?: ExtensionEventsAPI;

  // ---- 终端效果原语（TUI 宿主特有；其他宿主 undefined，判空降级）----
  /** 挂起 TUI 执行交互命令（终端让位——vim/htop 类；回执退出码）。 */
  runInteractive?(command: string): Promise<{ exitCode: number }>;
  /** 终端标题覆盖（undefined 清除，恢复宿主自动标题）。 */
  setTitle?(text: string | undefined): void;
  /** 桌面通知（OSC 9/777/99——受 desktop_notify 设置门控）。 */
  notifyDesktop?(title: string, body: string): void;

  // ---- 整件替换----
  /**
   * 自定义 footer（整件替换宿主默认 footer；undefined 恢复）。
   * 工厂收到 ``env``（FooterEnv：cwd/gitBranch/扩展状态/快照访问器）产出
   * 宿主组件（TUI：pi-tui Component——render(width)/invalidate，可带 dispose）。
   */
  setFooter?(factory: ((env: unknown) => unknown) | undefined): void;
  /**
   * 自定义 header（整件替换启动区 welcome；undefined 恢复）。
   * 工厂 env：{ cwd, getSnapshot, invoke }。
   */
  setHeader?(factory: ((env: unknown) => unknown) | undefined): void;

  // ---- loader 三旋钮（—流式期间的工作指示器定制）----
  /** 工作中文案（无参/undefined 恢复默认 "Working…"）。 */
  setWorkingMessage?(message?: string): void;
  /** spinner 帧/间隔定制（frames: [] 全隐藏指示器帧；undefined 恢复默认）。 */
  setWorkingIndicator?(options?: { frames?: string[]; intervalMs?: number }): void;
  /** 显示/隐藏内建 working loader 行。 */
  setWorkingVisible?(visible: boolean): void;
}

// 纪律：本接口只收"宿主原语"（后端 RPC 够不着的能力——主题/编辑器/状态行/
// 剪贴板/终端输入/终端效果等 Node 层自有件）。后端方法的访问面就是 ``invoke``——
// 全量方法经生成的 NovaWireMethodMap 带类型、随后端自动增长；
// 不得为后端方法手写包装域（双份维护 + 开放幻觉）。

/** 事件观察口（前端本地事实只读桥）。 */
export interface ExtensionEventsAPI {
  /**
   * 订阅后端事件（bus 观察式纪律：mirror 先行、异常隔离、``*`` 通配）。
   * 返回反注册函数；包重载（refreshPackages）时宿主统一注销全部订阅。
   */
  on(type: string, handler: (event: unknown) => void): () => void;
}

/** Node 扩展命令定义（registerCommand）。 */
export interface ExtensionCommandDef {
  description?: string;
  /** 参数补全（—slash 命令参数的建议条目）。 */
  getArgumentCompletions?(
    argumentPrefix: string,
  ):
    | Array<{ value: string; label: string; description?: string }>
    | null
    | Promise<Array<{ value: string; label: string; description?: string }> | null>;
  handler: (args: string, ctx: ExtensionUIContext) => void | Promise<void>;
}

/** 扩展快捷键 handler（键位动作——Node 本地执行）。 */
export type ExtensionShortcutHandler = (ctx: ExtensionUIContext) => void | Promise<void>;

/** 扩展设置声明（api.settings.define 的 def——schema 走代码，与"工具即代码"同纪律）。 */
export interface ExtensionSettingDef {
  type: 'string' | 'number' | 'boolean';
  default: unknown;
  description?: string;
}

/** 扩展设置面（用户可见配置——设置面板消费；Node 层前端域 settings.json 存储）。 */
export interface ExtensionSettingsAPI {
  /** 声明设置键（同 owner 幂等重载；异 owner 冲突返回 false + 诊断）。 */
  define(key: string, def: ExtensionSettingDef): boolean;
  /** 读（未显式设置时并入注册默认值）。 */
  get<T = unknown>(key: string): T | undefined;
  /** 写（未声明/类型不符拒绝——返回 false）。 */
  set(key: string, value: unknown): boolean;
}

/** 扩展内部 KV（命名空间隔离——扩展自己记来干活的数据，不进设置面板）。 */
export interface ExtensionStateAPI {
  get<T = unknown>(key: string): T | undefined;
  set(key: string, value: unknown): void;
  all(): Record<string, unknown>;
}

/**
 * custom 条目渲染器（entry:<customType> slot——扩展自定义条目的呈现）。
 *
 * 双形态（与工具渲染器契约同款）：
 * - ``NovaBlock[]``：声明式块（静态条目——数据变化时宿主按新数据重渲）；
 * - 活组件：pi-tui ``Component``（框架无关层以 ``object`` 承载），可选
 *   ``update(data)``——流式/可定稿条目（如 bashExecution：空数据创建、
 *   chunk 累积、message_end 完结）由宿主在条目数据变化时回调重绘
 *   ，组件身份不变。
 */
export type EntryRenderableComponent = object & { update?(data: unknown): void };
export type EntryRenderer = (entry: {
  customType: string;
  data: unknown;
}) => NovaBlock[] | EntryRenderableComponent;

/** overlay 锚点（与 pi-tui OverlayAnchor 同构——数据，可过网）。 */
export type NovaOverlayAnchor =
  | 'center'
  | 'top-left'
  | 'top-right'
  | 'bottom-left'
  | 'bottom-right'
  | 'top-center'
  | 'bottom-center'
  | 'left-center'
  | 'right-center';

/**
 * overlay 布局选项（—**纯数据子集**：
 * 框架无关层不收函数谓词（visible）——响应式显隐这类宿主交互逻辑
 * 属于同进程逃生舱，扩展可在组件内部自行处理）。
 */
export interface NovaOverlayOptions {
  /** 宽度（列数或百分比字符串 "50%"）。 */
  width?: number | `${number}%`;
  minWidth?: number;
  /** 最大高度（行数或百分比字符串）。 */
  maxHeight?: number | `${number}%`;
  /** 锚点（默认 center）。 */
  anchor?: NovaOverlayAnchor;
  offsetX?: number;
  offsetY?: number;
  row?: number | `${number}%`;
  col?: number | `${number}%`;
  margin?: number | { top?: number; right?: number; bottom?: number; left?: number };
  /** 不抢键盘焦点（被动面板——默认 false 即抢焦点）。 */
  nonCapturing?: boolean;
}

/** overlay 注册包装标记（region:overlay slot 值的信封——host 解包用）。 */
export const OVERLAY_WRAPPER: unique symbol = Symbol('nova.overlay');

/** overlay slot 值信封：组件 + 布局选项（host 经 unwrapOverlay 解包）。 */
export interface OverlayRegistration {
  [OVERLAY_WRAPPER]: true;
  component: unknown;
  options?: NovaOverlayOptions;
}

/** 解包 overlay slot 值（非信封原样返回 null——裸组件走默认布局）。 */
export function unwrapOverlay(value: unknown): OverlayRegistration | null {
  if (
    typeof value === 'object' &&
    value !== null &&
    (value as Record<symbol, unknown>)[OVERLAY_WRAPPER] === true
  ) {
    return value as OverlayRegistration;
  }
  return null;
}

/** 扩展 UI API（工厂入参）。注册来源与碰撞诊断由宿主注入。 */
export interface ExtensionUIAPI {
  /** 注册工具渲染器（等价 ``ui/renderers/<tool>.ts`` 的编程式形态）。 */
  registerRenderer(toolName: string, render: NovaRenderer, preview?: PreviewComputer): void;
  /** 注册区域部件（``footer`` 等区域——产出块列表，宿主区域渲染位消费）。 */
  registerRegion(region: string, producer: RegionProducer): void;
  /**
   * 注册区域部件的逃生舱形态：产出宿主组件（TUI：pi-tui Component）——
   * 与 registerRegion 同键竞争（后注册覆盖、碰撞诊断在案）。
   */
  registerRegionComponent(region: string, factory: RegionComponentFactory): void;
  /**
   * 注册 overlay 浮层：组件叠画在
   * 整个布局之上（不进文档流），布局经 options 声明（锚点/宽高/边距）。
   * 单例键（``region:overlay``）——后注册替换前者；TUI 宿主经
   * ``tui.showOverlay`` 呈现。逃生舱的一种（同进程全自由）。
   */
  registerOverlay(factory: RegionComponentFactory, options?: NovaOverlayOptions): void;
  /**
   * 注册自定义块类型（``block:<kind>``）：渲染器产出自定义 kind 的块，
   * 宿主块适配层经注册表解析到本适配器；``validate`` 提供 schema 守护。
   */
  registerBlock(kind: string, def: CustomBlockDef): void;
  /** 注册扩展编辑器（替换宿主默认编辑器——v1 每包一个 ``editor:main`` 键）。 */
  registerEditor(factory: EditorFactory): void;
  /** 注册 Node 扩展命令（进统一命令表——slash 补全与分发合并；撞名后注册赢 + 诊断）。 */
  registerCommand(name: string, def: ExtensionCommandDef): void;
  /**
   * 注册扩展快捷键（Node 本地执行——优先于内建键位路由）；
   * 撞保留键位（RESERVED_KEYBINDINGS）的注册在 keymap 对账时剔除 + 诊断。
   */
  registerShortcut(key: string, handler: ExtensionShortcutHandler): void;
  /** 扩展设置（用户可见配置——define 声明键，get/set 读写；Node 层存储）。 */
  readonly settings: ExtensionSettingsAPI;
  /** 扩展内部 KV（命名空间隔离——按包名分文件）。 */
  readonly state: ExtensionStateAPI;
  /** 注册 custom 条目渲染器（``entry:<customType>``——扩展 append_entry 条目的呈现）。 */
  registerEntryRenderer(customType: string, renderer: EntryRenderer): void;
  /**
   * 注册自动补全 provider（``autocomplete:<name>``）：
   * 扩展补全源组合进编辑器基线补全（slash/文件路径），建议条目排在基线之前。
   * provider 为宿主编辑器补全接口（TUI：pi-tui ``AutocompleteProvider``——框架无关层以 unknown 承载）。
   */
  registerAutocompleteProvider?(name: string, provider: unknown): void;
  /**
   * 注册自定义对话框（``dialog:<name>``）：
   * 后端（Python 工具/扩展）经 ``ui.request("dialog:<name>", params)`` 调起
   * 本工厂产出的组件；``done(result)`` 交还结果并关框（undefined = 取消，
   * 其余值按 ``{value: result}`` 应答）。注册即触发能力重宣告
   * （``system/capabilities``——后端 ``has_capability`` 随之放行）。
   */
  registerDialog?(name: string, factory: DialogFactory): void;
}

/**
 * 自定义对话框组件工厂：
 * ``env`` 为宿主对话框环境（TUI：RegionEnv——cwd/tui/colors/markdownTheme）；
 * ``params`` 为后端 ``ui.request`` 的原始参数；``done`` 交还结果。
 */
export type DialogFactory = (
  env: unknown,
  params: Record<string, unknown>,
  done: (result?: unknown) => void,
) => unknown;

/**
 * 构造扩展 UI API（闭包绑定注册表与来源）。
 * ``onCollision``：同键异源覆盖时回调（loader 收集为碰撞诊断——
 * 注册表保持纯覆盖语义，可见性归管线层）。
 */
export function createExtensionUIAPI(deps: {
  slots: SlotRegistry;
  source: string;
  onCollision?: (key: string, winner: string, loser: string) => void;
  /** 命令/快捷键执行上下文（宿主注入——loader 经 runtime 透传）。 */
  uiContext?: ExtensionUIContext;
  /** 扩展设置/状态存储（宿主注入——runtime 持有的子系统实例）。 */
  uiSettings?: UISettings;
  uiState?: UIStateStore;
  /** dialog:* 注册变化钩子（宿主注入——触发能力重宣告 system/capabilities）。 */
  onDialogChange?: () => void;
}): ExtensionUIAPI {
  const { slots, source, onCollision, uiContext, uiSettings, uiState, onDialogChange } = deps;
  const ctx: ExtensionUIContext = uiContext ?? {
    invoke: () => Promise.reject(new Error('命令上下文未注入')),
    notify: () => {},
  };
  const register = <I, O>(key: string, producer: SlotProducer<I, O>) => {
    const existing = slots.sourceOf(key);
    if (existing !== undefined && existing !== source) {
      onCollision?.(key, source, existing);
    }
    slots.register(key, producer, source);
  };
  return {
    registerRenderer: (toolName, render, preview) => {
      register(toolSlot(toolName), render);
      if (preview !== undefined) slots.registerToolPreview(toolName, preview, source);
    },
    registerRegion: (region, producer) => {
      register(regionSlot(region), producer);
    },
    registerRegionComponent: (region, factory) => {
      register(regionSlot(region), factory);
    },
    registerOverlay: (factory, options) => {
      // 包装为信封（组件 + 布局选项）——OverlayHost 解包后 showOverlay
      register(regionSlot('overlay'), (env: unknown) => ({
        [OVERLAY_WRAPPER]: true,
        component: factory(env),
        options,
      }));
    },
    registerBlock: (kind, def) => {
      register(blockSlot(kind), def.adapter);
      if (def.validate !== undefined) {
        slots.registerBlockValidator(kind, def.validate, source);
      }
    },
    registerEditor: (factory) => {
      register(editorSlot(), factory);
    },
    registerCommand: (name, def) => {
      // description 挂在函数对象上——补全目录需要真实描述（否则只能显示通用占位）
      const fn = (args: string) => def.handler(args, ctx);
      if (def.description !== undefined) {
        (fn as { description?: string }).description = def.description;
      }
      // 参数补全同样附着（editor 补全目录消费）
      if (def.getArgumentCompletions !== undefined) {
        (fn as { getArgumentCompletions?: unknown }).getArgumentCompletions =
          def.getArgumentCompletions;
      }
      register(commandSlot(name), fn);
    },
    registerShortcut: (key, handler) => {
      register(shortcutSlot(key), () => handler(ctx));
    },
    registerEntryRenderer: (customType, renderer) => {
      register(entrySlot(customType), renderer);
    },
    registerAutocompleteProvider: (name, provider) => {
      // provider 是对象而非生产者——包一层 thunk 适配 slot 注册表（resolve 后调用即得）
      register(autocompleteSlot(name), () => provider);
    },
    registerDialog: (name, factory) => {
      // 三元工厂（env/params/done）以生产者名义注册，消费点（dialogs 控制器）按
      // 真实签名调用——注册表对生产者签名不设防（同 autocomplete 的对象注册）
      register(dialogSlot(name), factory as unknown as SlotProducer);
      onDialogChange?.();
    },
    settings: {
      define: (key, def) => {
        if (!uiSettings) return false;
        const ok = uiSettings.define(key, def, source);
        if (!ok) {
          // 异 owner 冲突：复用碰撞诊断通道（设置键命名空间在案）
          onCollision?.(`setting:${key}`, source, uiSettings.registrations()
            .find((r) => r.key === key)?.def.owner ?? 'unknown');
        }
        return ok;
      },
      get: (key) => uiSettings?.get(key),
      set: (key, value) => uiSettings?.set(key, value) ?? false,
    },
    state: {
      get: (key) => uiState?.get(source, key),
      set: (key, value) => uiState?.set(source, key, value),
      all: () => uiState?.all(source) ?? {},
    },
  };
}
