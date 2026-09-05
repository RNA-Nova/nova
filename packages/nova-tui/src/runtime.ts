/**
 * NovaUIRuntime：Node 扩展运行时的进程内入口（facade，零业务）。
 *
 * 组装与接线，仅此而已：
 * - wire（client/bridge/capabilities）：与后端的唯一接触面；
 * - bus（脊柱）：后端事件上线，mirror 特权订阅；
 * - mirror（mirror/store）：事件 → 呈现模型；
 * - presentation（slots）：渲染器注册表；
 * - packages + extensions/loader：已安装包的 ui/ 渲染器加载。
 *
 * 实现 RuntimeHost（进程内宿主），nova-tui 直接消费。
 */

import { NovaBus } from './bus.js';
import { PackageRegistry } from './packages/registry.js';
import { fetchPackageUpdateNotice } from './packages/updates.js';
import { SlotRegistry } from './presentation/slots.js';
import type { ThemeJson } from './presentation/theme-json.js';
import {
  createExtensionUIAPI,
  type ExtensionUIContext,
  type ExtensionUIAPI,
  type NovaOverlayOptions,
} from './presentation/extension-api.js';
import { discoverLooseAssets, discoverUIAssets } from './resources/discovery.js';
import { loadUIAssets } from './resources/loader.js';
import { partitionByTrust } from './resources/trust.js';
import { projectFrontendDir, userFrontendDir } from './paths.js';
import type {
  ImageContent,
  NovaEventEnvelope,
  NovaWireMethod,
  NovaWireMethodMap,
} from './protocol/nova-wire.gen.js';
import { MirrorStore, type HistoryEntry } from './mirror/store.js';
import { UISettings, UIStateStore } from './settings/store.js';
import type { SessionSnapshot, StoreChange } from './mirror/types.js';
import { ReverseBridge, type UINotice, type UIRequest } from './wire/bridge.js';
import { CapabilitySet, checkContractVersion } from './wire/capabilities.js';
import {
  WireClient,
  type WireClientOptions,
  type WireParams,
  type WireResult,
} from './wire/client.js';

export interface NovaUIRuntimeOptions extends WireClientOptions {
  /** createSession 的参数（cwd/model/agentName 等）。 */
  session?: {
    cwd?: string;
    model?: string;
    agentName?: string;
    continueLast?: boolean;
    sessionFlag?: string;
    /** 恢复指定会话（--session——绝对路径直接用；裸 id 由后端在 cwd 会话目录解析）。 */
    sessionFile?: string | null;
    /** 思考级别（--thinking——直通 createSession.thinkingLevel）。 */
    thinkingLevel?: string | null;
    /** 临时会话（--no-session——内存态运行，不落盘不进会话列表）。 */
    noSession?: boolean;
    /** 扩展 flag 启动值（未声明长选项透传——契约 CreateSessionParams.extensionFlags）。 */
    extensionFlags?: Record<string, string | true>;
    agentDir?: string;
  };
  /**
   * slot 注册表引导（宿主注入 builtin 贡献——TUI 的块适配器族/内建命令等）。
   * 每次 slots 实例重建（refreshPackages 整体替换）后、包渲染器注册前
   * 调用——**内建与第三方走同一个 ExtensionUIAPI**（dogfood 终极形态：
   * bootstrap 拿到的就是包扩展的那个 api 对象），包可覆盖内建
   * （碰撞诊断在案）。宿主无关层不认识任何具体贡献，全靠此钩子注入。
   */
  slotsBootstrap?: (api: ExtensionUIAPI) => void;
  /** 启动时上报的反向原语能力（默认空集——以实际实现为准，M4 自动宣告）。 */
  capabilities?: string[];
  /** 扩展设置存储路径（测试注入隔离；默认 ~/.nova/agent/frontend/tui/settings.json）。 */
  uiSettingsPath?: string;
  /** 扩展内部 KV 目录（测试注入隔离；默认 ~/.nova/agent/frontend/tui/state/）。 */
  uiStateDir?: string;
  /** 包内主题资产回调（refreshPackages 收集完成后——宿主注册进主题系统）。 */
  onPackageThemes?: (themes: Map<string, ThemeJson>) => void;
  /**
   * 扩展命令上下文的宿主注入面（TUI 经本地对话框/编辑器/终端实现）。
   * 全部可选——缺省即该能力 undefined，扩展判空降级。
   */
  extensionUI?: {
    select(
      title: string,
      items: Array<{ value: string; label: string; description?: string }>,
    ): Promise<string | undefined>;
    confirm?(title: string, message: string): Promise<boolean>;
    input?(title: string, placeholder?: string): Promise<string | undefined>;
    editor?(title: string, prefill?: string): Promise<string | undefined>;
    custom?<T>(
      factory: (env: unknown, done: (result?: T) => void) => unknown,
      options?: { overlay?: NovaOverlayOptions },
    ): Promise<T | undefined>;
    getEditorText?(): string;
    setEditorText?(text: string): void;
    pasteToEditor?(text: string): void;
    writeClipboard?(text: string): Promise<boolean>;
    setStatus?(key: string, text: string | undefined): void;
    onTerminalInput?(handler: (data: string) => boolean | undefined): () => void;
    getToolsExpanded?(): boolean;
    setToolsExpanded?(expanded: boolean): void;
    getTheme?(): string;
    getAllThemes?(): Array<{ name: string; source: string }>;
    setTheme?(name: string): void;
    /** 登记前台在飞任务（Esc 域级路由消费；返回注销函数）。 */
    registerForegroundTask?(cancel: () => void): () => void;
    /** TUI 挂起/恢复执行交互命令（终端让位——TUI 宿主特有原语）。 */
    runInteractive?(command: string): Promise<{ exitCode: number }>;
    /** 终端标题覆盖（undefined 清除，恢复宿主自动标题）。 */
    setTitle?(text: string | undefined): void;
    /** 桌面通知（OSC 9/777/99——受 desktop_notify 设置门控）。 */
    notifyDesktop?(title: string, body: string): void;
    /** 自定义 footer（整件替换；undefined 恢复默认）。 */
    setFooter?(factory: ((env: unknown) => unknown) | undefined): void;
    /** 自定义 header（整件替换启动区；undefined 恢复默认）。 */
    setHeader?(factory: ((env: unknown) => unknown) | undefined): void;
    /** 工作指示器文案。 */
    setWorkingMessage?(message?: string): void;
    /** spinner 帧/间隔（frames: [] 隐藏帧）。 */
    setWorkingIndicator?(options?: { frames?: string[]; intervalMs?: number }): void;
    /** 显示/隐藏内建 working loader 行。 */
    setWorkingVisible?(visible: boolean): void;
  };
}

/** setThinkingLevel 的合法取值（契约生成的思考级别枚举）。 */
export type ThinkingLevel = NovaWireMethodMap['setThinkingLevel']['params']['level'];

/** steering/follow-up 模式的合法取值（契约生成枚举）。 */
export type QueueMode = NovaWireMethodMap['setSteeringMode']['params']['mode'];

/**
 * RuntimeHost：前端（TUI）面对的唯一接口（设计 v3 §11）。
 *
 * 本类是进程内实现（nova-tui 直接消费）。前端能做的事收敛在四组方法里：
 * 读模型 / 订阅变更 / 调命令 / 应答原语。
 */
export interface RuntimeHost {
  /** 会话镜像（transcript/status/快照的只读来源）。 */
  readonly store: MirrorStore;
  /** slot 注册表（工具/条目渲染器与区域部件的解析入口）。 */
  readonly slots: SlotRegistry;

  /** 订阅呈现模型变更。 */
  subscribe(listener: (change: StoreChange) => void): () => void;

  /** 统一命令表调用（类型化：方法名/params/result 走契约生成表）。 */
  invoke<M extends NovaWireMethod>(
    method: M,
    params?: WireParams<M>,
  ): Promise<WireResult<M>>;

  /** 反向原语请求转交（前端实现对话框后 sendUIResponse 应答）。 */
  onUIRequest(handler: (request: UIRequest) => void): void;
  sendUIResponse(id: string, result: unknown): void;

  /** 单向通知转交（ui/notify：进度/警告/设备码展示等）。 */
  onUINotice(handler: (notice: UINotice) => void): void;

  /** 撤销转交（后端 abort 竞速胜出 → 关闭对应 id 的对话框）。 */
  onUICancel(handler: (id: string) => void): void;

  /** 后端进程退出通知（弹提示/退出策略归前端；M3 的断线重连也挂这里）。 */
  onClose(handler: () => void): void;
}

export class NovaUIRuntime implements RuntimeHost {
  readonly client: WireClient;
  readonly bus: NovaBus;
  readonly store: MirrorStore;
  /**
   * slot 注册表。**整体替换**是 reload 语义的一部分（refreshPackages
   * 时换新实例防卸载残留）——消费者勿持有引用，经 ``runtime.slots``
   * 现取（接口上的 readonly 是对消费者的承诺，内部可替换）。
   */
  slots: SlotRegistry;
  readonly packages: PackageRegistry;
  /** 扩展设置/内部 KV 子系统（design §7——Node 层存储，不背进后端 settings）。 */
  readonly uiSettings: UISettings;
  readonly uiState: UIStateStore;
  private readonly bridge: ReverseBridge;
  private caps: CapabilitySet | undefined;
  private closeHandler: (() => void) | undefined;
  /** slots 整体替换完成通知（refreshPackages 成功路径——扩展编辑器热替换等）。 */
  private readonly slotsReplacedHandlers: Array<() => void> = [];

  constructor(private readonly options: NovaUIRuntimeOptions = {}) {
    this.client = new WireClient(options);
    this.bus = new NovaBus();
    this.bridge = new ReverseBridge(this.client);
    this.store = new MirrorStore(this.bus);
    this.slots = this.createSlots();
    this.packages = new PackageRegistry(this.client);
    this.uiSettings = new UISettings(options.uiSettingsPath ?? UISettings.defaultPath());
    this.uiState = new UIStateStore(options.uiStateDir ?? UIStateStore.defaultDir());
    // 设置变更 → bus 派生事件（扩展与前端可订阅）
    this.uiSettings.onChange((key, value) =>
      this.bus.publishDerived('settings:changed', { key, value }),
    );
  }

  /** 新建 slot 注册表并注入宿主 builtin 贡献（refreshPackages 整体替换同路径）。 */
  private createSlots(): SlotRegistry {
    const slots = new SlotRegistry();
    if (this.options.slotsBootstrap) {
      // 内建扩展与包扩展同一个 api 对象（dogfood——无第二通道）
      const api = createExtensionUIAPI({
        slots,
        source: 'builtin',
        uiContext: this.buildUIContext(),
        uiSettings: this.uiSettings,
        uiState: this.uiState,
        onDialogChange: () => this.refreshDialogCapabilities(),
      });
      this.options.slotsBootstrap(api);
    }
    return slots;
  }

  /**
   * 当前应宣告的 UI 能力全集：基线五件套（options.capabilities——宿主声明）
   * + 已注册的 ``dialog:*`` 键（键即线上 componentType——包侧自定义对话框）。
   * 注册变化即重宣告（system/capabilities——后端 has_capability 实时放行）。
   */
  private advertisedCapabilities(): string[] {
    const baseline = this.options.capabilities ?? [];
    const dialogNames = this.slots
      .list()
      .map((entry) => entry.key)
      .filter((key) => key.startsWith('dialog:'));
    return [...baseline, ...dialogNames];
  }

  /** dialog:* 注册/重载后的能力重宣告（后端 ui_context.update_capabilities）。 */
  private refreshDialogCapabilities(): void {
    this.bridge.sendCapabilities(this.advertisedCapabilities());
  }

  /** 扩展 UI 上下文（createSlots 与 refreshPackages 共用——全部可选，宿主未注入即 undefined）。 */
  private buildUIContext(): ExtensionUIContext {
    const host = this.options.extensionUI;
    return {
      invoke: (method, params) => this.client.call(method as never, (params ?? {}) as never),
      invokeCancellable: (method, params) =>
        this.client.callCancellable(method as never, (params ?? {}) as never),
      notify: (message, level) =>
        this.store.addNotice(level === 'error' ? 'error' : 'info', message),
      select: host?.select,
      refreshPackages: () => this.refreshPackages(),
      registerForegroundTask: host?.registerForegroundTask,
      confirm: host?.confirm,
      input: host?.input,
      editor: host?.editor,
      custom: host?.custom,
      getEditorText: host?.getEditorText,
      setEditorText: host?.setEditorText,
      pasteToEditor: host?.pasteToEditor,
      writeClipboard: host?.writeClipboard,
      setStatus: host?.setStatus,
      onTerminalInput: host?.onTerminalInput,
      getToolsExpanded: host?.getToolsExpanded,
      setToolsExpanded: host?.setToolsExpanded,
      getTheme: host?.getTheme,
      getAllThemes: host?.getAllThemes,
      setTheme: host?.setTheme,
      // 事件观察口：bus 只读桥（订阅登记在册——包重载时统一注销，防泄漏）
      events: {
        on: (type, handler) => {
          const off = this.bus.on(type as never, handler as never);
          this.extensionEventUnsubs.add(off);
          return () => {
            off();
            this.extensionEventUnsubs.delete(off);
          };
        },
      },
      runInteractive: host?.runInteractive?.bind(host),
      setTitle: host?.setTitle?.bind(host),
      notifyDesktop: host?.notifyDesktop?.bind(host),
      setFooter: host?.setFooter?.bind(host),
      setHeader: host?.setHeader?.bind(host),
      setWorkingMessage: host?.setWorkingMessage?.bind(host),
      setWorkingIndicator: host?.setWorkingIndicator?.bind(host),
      setWorkingVisible: host?.setWorkingVisible?.bind(host),
    };
  }

  /** 扩展事件订阅登记处（refreshPackages 整体替换 slots 时统一注销）。 */
  private readonly extensionEventUnsubs = new Set<() => void>();

  /** 事件高水位（最近一次 syncSession 的原子锚点）：seq ≤ 水位的增量
   * 事件已反映在快照内，事件汇直接丢弃（防快照/增量重复应用）。 */
  private syncWatermark = 0;

  /** 后端能力位（start 后可用——前端按位降级功能入口）。 */
  get capabilities(): CapabilitySet | undefined {
    return this.caps;
  }

  /** 启动后端、握手、创建会话、全量同步、加载包渲染器。 */
  async start(): Promise<void> {
    // 后端事件上线：wire → bus（mirror 经特权订阅先行应用）。
    // 水位对账（连接化 P2）：seq ≤ syncWatermark 的事件已包含在最近一次
    // syncSession 快照里（原子锚点）——丢弃，防止快照/增量重复应用
    this.client.onEvent((event: NovaEventEnvelope) => {
      if (typeof event.seq === 'number' && event.seq <= this.syncWatermark) return;
      this.bus.publish(event);
    });
    // 后端进程退出 → 转交前端（在飞的调用已被 client 全部 reject）
    this.client.onClose(() => this.closeHandler?.());

    await this.client.start();
    const handshake = await this.client.call('initialize', {});
    checkContractVersion(handshake);
    this.caps = new CapabilitySet(handshake);

    this.bridge.sendCapabilities(
      // 诚实宣告：默认空集——以实际实现为准（基线由宿主显式传入，包侧
      // 自定义对话框经 dialog:* slot 实时并入）；默认全支持会让后端误以为
      // 有 UI，把 headless 该走的路径（拦截/拒绝）错走成"发问后挂起"。
      this.advertisedCapabilities(),
    );

    await this.client.call('createSession', this.options.session ?? {});

    await this.syncFromBackend();

    // 后台加载（不阻塞启动）：包索引 → ui/ 渲染器；启动更新提醒
    void this.refreshPackages();
    void this.checkPackageUpdates();
  }

  /** 全量同步：原子快照（syncSession：状态 + 条目分页 + 事件高水位）→ mirror。
   * 启动与 session_replaced 后调用。旧后端（无 syncSession，MINOR<3）回退
   * 两发路径（getSessionState + getSessionEntries，无水位对账）。 */
  async syncFromBackend(): Promise<void> {
    const pageSize = 500;
    try {
      let offset = 0;
      let snapshot: SessionSnapshot | undefined;
      let entries: HistoryEntry[] = [];
      // 逐页翻完：快照与水位每页刷新——末页三者（state/entries/eventSeq）
      // 同一时点，天然原子（后端 handler 同步段无 await）
      for (;;) {
        const page = await this.client.call('syncSession', {
          entriesOffset: offset,
          entriesLimit: pageSize,
        });
        snapshot = page.state as unknown as SessionSnapshot;
        this.syncWatermark = page.eventSeq;
        entries = entries.concat(page.entries as HistoryEntry[]);
        if (page.entries.length === 0 || offset + page.entries.length >= page.total)
          break;
        offset += page.entries.length;
      }
      if (snapshot === undefined) return;
      this.store.sync(snapshot, entries);
    } catch (error) {
      // MINOR 差放行下的混合版本：旧后端无 syncSession（-32601）回退两发路径
      if (error instanceof Error && error.message.includes('-32601')) {
        const [snapshot, history] = await Promise.all([
          this.client.call('getSessionState', {}),
          this.client.call('getSessionEntries', {}),
        ]);
        this.store.sync(snapshot, history.entries as HistoryEntry[]);
        return;
      }
      throw error;
    }
  }

  /** 重拉快照（设置类命令后——settings 可改的字段面不设防地全量对账）。 */
  async refreshSnapshot(): Promise<void> {
    const snapshot = await this.client.call('getSessionState', {});
    this.store.updateSnapshot(snapshot);
  }

  /** 刷新包索引并（重）加载所有已安装包的 ui/ 渲染器。 */
  async refreshPackages(): Promise<void> {
    try {
      const installed = await this.packages.refresh();
      const trusted = this.store.currentSnapshot?.projectTrusted ?? false;
      const discovered = (
        await Promise.all(installed.map((pkg) => discoverUIAssets(pkg)))
      ).filter((a) => a !== null);

      // 散养资产根（前后端分治 §9 新增扫描能力）：user 级永远可信；
      // project 级未信任不 stat 不 import（发现门在扫描之前——比加载期
      // 过滤更上游）。注册顺序即覆盖优先级：散养排在包之后、project 散养
      // 最后（project > user > package > builtin）
      const cwd = this.options.session?.cwd ?? process.cwd();
      discovered.push(
        ...(await discoverLooseAssets({
          userRoot: userFrontendDir(),
          projectRoot: projectFrontendDir(cwd),
          trusted,
        })),
      );

      // trust 过滤（编排层）：project 级包不被
      // 信任时不进加载域——不 stat、不 import
      const { allowed, skipped } = partitionByTrust(discovered, trusted);
      for (const name of skipped) {
        this.store.addNotice('info', `渲染器未加载（项目不被信任）：${name}`);
      }

      // 注册表整体替换（对齐后端 ToolsManager.refresh 的原子形态）：
      // reload 时卸载包的渲染器不残留（增量覆盖只增不减的解药）；
      // builtin 贡献经 slotsBootstrap 随新实例重生（先于包渲染器注册）
      for (const off of this.extensionEventUnsubs) off();
      this.extensionEventUnsubs.clear();
      this.slots = this.createSlots();
      const result = await loadUIAssets(allowed, this.slots, {
        uiContext: this.buildUIContext(),
        uiSettings: this.uiSettings,
        uiState: this.uiState,
        onDialogChange: () => this.refreshDialogCapabilities(),
        // npm 自愈后台任务的完成回调：补齐则刷新让渲染器上线；失败通知
        // 带修复指引（渲染器本轮已按诊断降级为通用显示）
        onNpmHealed: (name, ok) => {
          if (ok) {
            this.store.addNotice(
              'info',
              `包 ${name} 的呈现依赖已补齐——重新加载渲染器`,
            );
            void this.refreshPackages();
          } else {
            this.store.addNotice(
              'error',
              `包 ${name} 的 npm 依赖补装失败（无 npm 或网络问题）——渲染器降级为通用显示；装 Node 后 /reload 或重装该包重试`,
            );
          }
        },
      });
      // 诊断统一透出（加载失败 + 覆盖碰撞）
      for (const diagnostic of result.diagnostics) {
        this.store.addNotice(
          diagnostic.type === 'error' ? 'error' : 'info',
          diagnostic.message,
        );
      }
      // 替换完成通知（扩展编辑器热替换等——仅成功路径；catch 里实例未换）
      this.options.onPackageThemes?.(result.themes); // 包内主题资产透出宿主
      for (const handler of this.slotsReplacedHandlers) handler();
    } catch (error) {
      // 后端无 package 域 / 离线：渲染器回退到通用空态，不阻塞启动；
      // 但静默吞错会把真实加载失败藏成"0 packages"——透出诊断
      this.store.addNotice(
        'error',
        `包 UI 资源加载失败：${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  /** slots 整体替换完成通知（扩展编辑器热替换等场景）。 */
  onSlotsReplaced(handler: () => void): void {
    this.slotsReplacedHandlers.push(handler);
  }

  /** 包更新提醒：产品逻辑在 packages/updates，这里只做"文本 → notice"编排。 */
  async checkPackageUpdates(): Promise<void> {
    const notice = await fetchPackageUpdateNotice(this.client);
    if (notice !== null) this.store.addNotice('info', notice);
  }

  async stop(): Promise<void> {
    this.store.dispose();
    await this.client.stop();
  }

  // ------------------------------------------------------------------
  // RuntimeHost
  // ------------------------------------------------------------------

  subscribe(listener: (change: StoreChange) => void): () => void {
    return this.store.subscribe(listener);
  }

  async invoke<M extends NovaWireMethod>(
    method: M,
    params?: WireParams<M>,
  ): Promise<WireResult<M>> {
    return this.client.call(method, params);
  }

  /**
   * 可取消调用（LSP $/cancelRequest 对位的泛型形态）：navigateTree（分支摘要）
   * 等长 RPC 的 Esc 取消入口。cancel 本地立即 AbortError 收尾 + 上行
   * cancelRequest 请后端取消（幂等）。
   */
  invokeCancellable<M extends NovaWireMethod>(
    method: M,
    params?: WireParams<M>,
  ): { promise: Promise<WireResult<M>>; cancel: () => void } {
    return this.client.callCancellable(method, params);
  }

  onUIRequest(handler: (request: UIRequest) => void): void {
    this.bridge.onRequest(handler);
  }

  sendUIResponse(id: string, result: unknown): void {
    this.bridge.respond(id, result);
  }

  onUINotice(handler: (notice: UINotice) => void): void {
    this.bridge.onNotice(handler);
  }

  onUICancel(handler: (id: string) => void): void {
    this.bridge.onCancel(handler);
  }

  onClose(handler: () => void): void {
    this.closeHandler = handler;
  }

  // ------------------------------------------------------------------
  // 命令 API（类型化薄转发，与后端方法表同名；
  // 响应携带变更后事实的命令顺手回写快照——响应即事实，不猜增量）
  // ------------------------------------------------------------------

  /**
   * 发送用户消息。流式中必须给 ``streamingBehavior``（后端否则抛错）：
   * ``steer`` 插入当前 turn / ``followUp`` 排队等 run 结束。
   */
  async prompt(
    text: string,
    options?: {
      images?: ImageContent[];
      streamingBehavior?: 'steer' | 'followUp';
    },
  ): Promise<void> {
    await this.client.call('prompt', {
      text,
      ...(options?.images ? { images: options.images } : {}),
      ...(options?.streamingBehavior
        ? { streamingBehavior: options.streamingBehavior }
        : {}),
    });
  }

  /** 清空 steering/follow-up 队列并返回被清内容（dequeue/Esc 还原用）。 */
  async clearQueue(): Promise<{ steering: string[]; followUp: string[] }> {
    return (await this.client.call('clearQueue', {})) as {
      steering: string[];
      followUp: string[];
    };
  }

  /**
   * slash 命令专用：可取消的 prompt 调用（OAuth 登录等长命令的 Esc 入口）。
   * 普通对话请用 prompt——run 的取消语义是 abort（领域清理），不是取消调用。
   */
  promptCancellable(text: string): { promise: Promise<unknown>; cancel: () => void } {
    return this.client.callCancellable('prompt', { text });
  }

  async abort(): Promise<void> {
    await this.client.call('abort', {});
  }

  /** 域级 abort：只停自动重试等待（不动 run/压缩/用户工具）。 */
  async abortRetry(): Promise<void> {
    await this.client.call('abortRetry', {});
  }

  /** 域级 abort：只停上下文压缩（不动 run/retry/用户工具）。 */
  async abortCompaction(): Promise<void> {
    await this.client.call('abortCompaction', {});
  }

  async steer(text: string, images?: ImageContent[]): Promise<void> {
    await this.client.call('steer', { text, ...(images ? { images } : {}) });
  }

  async followUp(text: string): Promise<void> {
    await this.client.call('followUp', { text });
  }

  async setModel(model: string): Promise<boolean> {
    const result = await this.client.call('setModel', { model });
    return result.ok;
  }

  async cycleModel(direction: 'forward' | 'backward' = 'forward'): Promise<unknown> {
    return this.client.call('cycleModel', { direction });
  }

  async setThinkingLevel(level: ThinkingLevel): Promise<void> {
    await this.client.call('setThinkingLevel', { level });
  }

  /** 循环思考级别（后端按当前模型支持面循环——shift+tab）。 */
  async cycleThinkingLevel(): Promise<void> {
    await this.client.call('cycleThinkingLevel', {});
  }

  async compact(customInstructions?: string): Promise<unknown> {
    return this.client.call('compact', { customInstructions });
  }

  async newSession(): Promise<void> {
    await this.client.call('newSession', {});
    await this.syncFromBackend();
  }

  async setActiveTools(toolNames: string[]): Promise<boolean> {
    const result = await this.client.call('setActiveTools', { toolNames });
    if (result.ok) this.store.updateSnapshot({ activeTools: result.activeTools });
    return result.ok;
  }

  async setAutoRetry(enabled: boolean): Promise<boolean> {
    const result = await this.client.call('setAutoRetry', { enabled });
    if (result.ok) this.store.updateSnapshot({ autoRetryEnabled: result.autoRetryEnabled });
    return result.ok;
  }

  async setSteeringMode(mode: QueueMode): Promise<boolean> {
    const result = await this.client.call('setSteeringMode', { mode });
    if (result.ok) this.store.updateSnapshot({ steeringMode: result.steeringMode });
    return result.ok;
  }

  async setFollowUpMode(mode: QueueMode): Promise<boolean> {
    const result = await this.client.call('setFollowUpMode', { mode });
    if (result.ok) this.store.updateSnapshot({ followUpMode: result.followUpMode });
    return result.ok;
  }

  /** 设置写（可改字段面不设防）→ 全量重拉快照对账。 */
  async updateSettings(settings: Record<string, unknown>): Promise<boolean> {
    const result = await this.client.call('updateSettings', { settings });
    if (result.ok) await this.refreshSnapshot();
    return result.ok;
  }

  async invokeUserTool(name: string, params?: Record<string, unknown>): Promise<unknown> {
    return this.client.call('invokeUserTool', { name, params });
  }

  async setSessionName(name: string): Promise<unknown> {
    return this.client.call('setSessionName', { name });
  }
}
