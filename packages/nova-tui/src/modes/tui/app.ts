/**
 * NovaTuiApp —— 装配根（composition root）。
 *
 * 纪律（模块化硬纪律，防 6000 行编排层）：
 * - 本类只做装配：创建组件/控制器、接线、启动；
 * - 一切交互逻辑归 controllers/（editor/keymap/dialogs/transcript/status/pickers）；
 * - 组件零编排（自管渲染与自身输入，协作经 controller）。
 */

import { NovaUIRuntime } from 'nova-tui';
import type { ImageContent } from '../../protocol/nova-wire.gen.js';
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  Container,
  Editor,
  ProcessTerminal,
  TUI,
  setKeybindings,
  type Component,
} from '@earendil-works/pi-tui';

import { DialogController } from './controllers/dialogs.js';
import { EditorController, type EditorRef } from './controllers/editor.js';
import { ForegroundTasks } from './controllers/foreground.js';
import { KeymapController } from './controllers/keymap.js';
import { SettingsController } from './controllers/settings.js';
import { StatusController } from './controllers/status.js';
import { ThemeController } from './controllers/theme.js';
import { TranscriptController } from './controllers/transcript.js';
import { NovaKeybindingsManager } from '../../keymap/manager.js';
import { migrateFrontendLayout } from '../../migration.js';
import { NOVA_KEYBINDINGS, RESERVED_KEYBINDINGS } from './keymap/tables.js';
import {
  colors,
  editorTheme,
  getAvailableThemes,
  getCurrentThemeName,
  markdownTheme,
  onThemeChange,
  registerPackageThemes,
  setEditorBorderColorHook,
  thinkingBorderColor,
} from './themes/index.js';
import { registerBuiltinBlocks } from './blocks/index.js';
import { registerPackagePanel } from './builtin/package-panel.js';
import { FooterView } from './components/status/footer.js';
import { WelcomeView } from './components/layout/welcome.js';
import { OverlayHost } from './components/layout/overlay-host.js';
import { RegionHost } from './components/layout/region-host.js';
import { ResourcesView } from './components/layout/resources.js';
import { createPendingSlot } from './components/transcript/pending-messages.js';
import { SearchableSelector } from './components/pickers/searchable.js';
import { checkTmuxKeyboardSetup, checkTmuxExtendedKeys } from './utils/terminal-guard.js';
import { writeClipboardText } from './utils/clipboard.js';
import { installSignalHandlers } from './utils/signals.js';
import { StartupController } from './controllers/startup.js';
import {
  clearTerminalProgress,
  initTerminalIntegration,
  notifyTurnEnded,
  setTitleOverride,
  updateProgress,
  updateTitle,
} from './controllers/terminal.js';
import { getWhatsNewIfNeeded, renderChangelogEntry } from './utils/changelog.js';
import { initTuiSettings } from './utils/tui-settings.js';
import { bindTerminalThemeSync, watchThemeFiles } from './themes/index.js';
import type { ExpansionState } from './components/transcript/expansion.js';

export interface NovaTuiAppOptions {
  cwd: string;
  model?: string;
  agentName?: string;
  continueLast?: boolean;
  /** 启动后立即发送的首条消息（CLI positional——pi initialMessage 对位）。 */
  initialMessage?: string;
  /** 首条消息的图片附件（@file 图片参数——pi initialImages 对位）。 */
  initialImages?: ImageContent[];
  /** --session <file|id>：恢复指定会话（createSession.sessionFile——D 流契约就绪）。 */
  sessionFile?: string;
  /** --thinking <level>：思考级别（createSession.thinkingLevel）。 */
  thinking?: string;
  /** -r/--resume：启动后打开会话选择器。 */
  resume?: boolean;
  /** -n/--name <name>：启动后 setSessionName。 */
  sessionName?: string;
  /** --no-session：不持久化（挂账——契约无开关，仅警告）。 */
  noSession?: boolean;
}

/** 版本读取（包根 package.json——app.js 位于 dist/modes/tui/ 或 src/modes/tui/，上三级均为包根）。 */
function readPackageVersion(): string {
  try {
    const pkg = JSON.parse(
      readFileSync(new URL('../../../package.json', import.meta.url), 'utf-8'),
    ) as { version?: unknown };
    return typeof pkg.version === 'string' ? pkg.version : '';
  } catch {
    return '';
  }
}

export class NovaTuiApp {
  private readonly tui: TUI;
  private readonly runtime: NovaUIRuntime;
  private readonly footer: FooterView;
  private readonly transcript: TranscriptController;
  private readonly keymap: KeymapController;
  private readonly headerContainer: Container;
  private readonly expansion: ExpansionState = { expanded: false };
  /** 扩展原始终端输入拦截器集合（pi onTerminalInput 对位——keymap 之前优先消费）。 */
  private readonly terminalInputHandlers = new Set<(data: string) => boolean | undefined>();
  /** 前台在飞任务登记处（Esc 域级路由一环——分支摘要/gist 创建等非 run 任务）。 */
  private readonly foregroundTasks = new ForegroundTasks();
  private readonly welcome: WelcomeView;
  private readonly resources: ResourcesView;
  private readonly dialogs: DialogController;
  private headerRegionHost!: RegionHost;
  private statusController!: StatusController;
  /** setHeader 的自定义头部组件（整件替换 welcome——dispose 语义与 footer 同）。 */
  private headerOverride: (Component & { dispose?(): void }) | undefined;

  constructor(private readonly options: NovaTuiAppOptions) {
    // 前端域迁移（前后端分治 §9）：旧位状态/资产（ui-settings/ui-state/
    // keybindings/themes）搬入 frontend/tui/ 半区——必须先于键位表与
    // UISettings 的首次读取；消息暂存，transcript 就绪后透出
    this.migrationMessages = migrateFrontendLayout(options.cwd);

    // 键位必须先于组件创建（pi-tui 组件内部读全局键位表）；
    // 三级合并（builtin < user < project 两级前端域 keybindings.json），诊断在 transcript 就绪后显示
    const { manager: keybindings, diagnostics } = NovaKeybindingsManager.create(options.cwd, {
      defaults: NOVA_KEYBINDINGS,
      reserved: RESERVED_KEYBINDINGS,
    });
    setKeybindings(keybindings);
    this.keybindingDiagnostics = diagnostics;

    this.tui = new TUI(new ProcessTerminal());

    // —— 后端运行时（NOVA_PYTHON 指定后端解释器；生产为装 nova-harness 的环境）——
    const python = process.env.NOVA_PYTHON ?? 'python3';
    this.runtime = new NovaUIRuntime({
      command: [python, '-m', 'nova_harness.modes.rpc.cli'],
      capabilities: ['select', 'confirm', 'input', 'notify', 'form', 'set_status'],
      session: {
        cwd: options.cwd,
        model: options.model,
        agentName: options.agentName,
        continueLast: options.continueLast,
        sessionFile: options.sessionFile ?? null,
        thinkingLevel: options.thinking ?? null,
        ...(options.noSession ? { noSession: true } : {}),
      },
      // 宿主 builtin 贡献：官方块适配器族 + 内建包面板（与第三方同一 ExtensionUIAPI）
      slotsBootstrap: (api) => {
        registerBuiltinBlocks(api);
        registerPackagePanel(api);
      },
      // 包内主题资产（ui/themes/*.json）注册进主题系统（三源：builtin > 用户目录 > 包）
      onPackageThemes: (themes) => registerPackageThemes(themes),
      // 扩展 UI 上下文的宿主实现（全部懒绑定——闭包在扩展命令触发时才现取 controllers）
      extensionUI: {
        select: (title, items) => this.dialogs.selectLocal(title, items),
        confirm: (title, message) => this.dialogs.confirmLocal(title, message),
        input: (title, placeholder) => this.dialogs.inputLocal(title, placeholder),
        editor: (title, prefill) => this.dialogs.editorLocal(title, prefill),
        custom: (factory, opts) => this.dialogs.customLocal(factory, opts),
        getEditorText: () => this.editorRef.current.getText(),
        setEditorText: (text) => this.editorRef.current.setText(text),
        pasteToEditor: (text) => this.editorRef.current.insertTextAtCursor?.(text),
        writeClipboard: (text) => writeClipboardText(text),
        setStatus: (key, text) => {
          this.footer.setExtensionStatus(key, text);
          this.tui.requestRender();
        },
        onTerminalInput: (handler) => {
          this.terminalInputHandlers.add(handler);
          return () => this.terminalInputHandlers.delete(handler);
        },
        registerForegroundTask: (cancel) => this.foregroundTasks.register(cancel),
        getToolsExpanded: () => this.expansion.expanded,
        setToolsExpanded: (expanded) => this.setExpansion(expanded),
        getTheme: () => getCurrentThemeName(),
        getAllThemes: () =>
          getAvailableThemes().themes.map((theme) => ({ name: theme.name, source: theme.source })),
        setTheme: (name) => this.themeController.applyTheme(name),
        // 终端让位（interactive-shell 对话框同款序列——提升为正式原语）：
        // tui.stop 交出终端 → 清屏 → spawnSync(stdio 继承) → start + 重绘恢复
        runInteractive: async (command) => {
          const { spawnSync } = await import('node:child_process');
          this.tui.stop();
          process.stdout.write('\x1b[2J\x1b[H');
          const result = spawnSync(process.env.SHELL || '/bin/sh', ['-c', command], {
            stdio: 'inherit',
            cwd: options.cwd,
          });
          this.tui.start();
          this.tui.requestRender(true);
          // status 被信号杀死为 null（?? 1）；spawn 失败（error）也按 1
          return { exitCode: result.error ? 1 : (result.status ?? 1) };
        },
        setTitle: (text) => setTitleOverride(text),
        notifyDesktop: (title, body) => notifyTurnEnded(title, body),
        setFooter: (factory) => {
          this.footer.setCustomFooter(factory);
          this.tui.requestRender();
        },
        // 整件替换启动区 welcome（pi setHeader 对位——RegionHost('header') 不受影响）
        setHeader: (factory) => {
          this.headerOverride?.dispose?.();
          this.headerOverride = undefined;
          this.headerContainer.clear();
          if (factory !== undefined) {
            const env = {
              cwd: options.cwd,
              getSnapshot: () => this.runtime.store.currentSnapshot,
              invoke: (method: string, params?: Record<string, unknown>) =>
                this.runtime.invoke(method as never, params as never),
            };
            this.headerOverride = factory(env) as typeof this.headerOverride;
            if (this.headerOverride) this.headerContainer.addChild(this.headerOverride);
          } else {
            this.headerContainer.addChild(this.welcome);
          }
          this.headerContainer.addChild(this.resources);
          this.headerContainer.addChild(this.headerRegionHost);
          this.tui.requestRender();
        },
        setWorkingMessage: (message) => this.statusController.setWorkingMessage(message),
        setWorkingIndicator: (opts) => this.statusController.setWorkingIndicator(opts),
        setWorkingVisible: (visible) => this.statusController.setWorkingVisible(visible),
      },
    });
    this.runtime.onClose(() => this.quit(1));

    // —— 槽位布局（顺序即视觉顺序；editorContainer 与对话框共享槽位）——
    // welcome 启动区独立容器（quiet_startup 时 run() 移除；模型/展开态变化经 refresh）
    this.headerContainer = new Container();
    const chatContainer = new Container();
    const statusContainer = new Container();
    // widget 区（pi setWidget 对位：编辑器上方的扩展部件槽位——RegionHost 两态消费）
    const widgetContainer = new Container();
    const editorContainer = new Container();
    // widgetBelow 区（pi setWidget 的 belowEditor placement 对位——编辑器下方）
    const widgetBelowContainer = new Container();
    // EditorRef 盒子：controllers 共享当前编辑器（扩展编辑器热替换经换盒内容生效）
    this.editorRef = { current: new Editor(this.tui, editorTheme, { paddingX: 1 }) };
    editorContainer.addChild(this.editorRef.current);
    this.footer = new FooterView(this.runtime, options.cwd, () => this.currentSettings);

    this.tui.addChild(this.headerContainer);
    this.tui.addChild(chatContainer);
    this.tui.addChild(createPendingSlot(this.runtime));
    this.tui.addChild(statusContainer);
    this.tui.addChild(widgetContainer);
    this.tui.addChild(editorContainer);
    this.tui.addChild(widgetBelowContainer);
    this.tui.addChild(this.footer);

    // —— 区域部件宿主（逃生舱泛化消费点：header/widget/status/widgetBelow）——
    const regionEnv = {
      cwd: options.cwd,
      tui: this.tui,
      colors,
      markdownTheme,
    };
    this.headerRegionHost = new RegionHost(this.runtime, 'header', regionEnv);
    this.headerContainer.addChild(this.headerRegionHost);
    widgetContainer.addChild(new RegionHost(this.runtime, 'widget', regionEnv));
    widgetBelowContainer.addChild(new RegionHost(this.runtime, 'widgetBelow', regionEnv));
    statusContainer.addChild(new RegionHost(this.runtime, 'status', regionEnv));
    // overlay 浮层宿主（零高度——registerOverlay 的生命周期管理点）
    this.tui.addChild(new OverlayHost(this.runtime, this.tui, regionEnv));

    // —— controllers 装配（编排逻辑全部归位）——
    const expansion = this.expansion;
    this.transcript = new TranscriptController(
      this.tui,
      chatContainer,
      this.runtime,
      expansion,
      () => this.currentSettings.hideThinkingBlock === true,
    );
    const status = new StatusController(this.tui, statusContainer, this.runtime);
    this.statusController = status;
    this.dialogs = new DialogController(
      this.tui,
      editorContainer,
      this.editorRef,
      status,
      this.runtime,
      regionEnv, // custom 原语工厂环境（与区域宿主同一 RegionEnv）
      (key, text) => {
        // set_status 命名通知出口（后端驱动的 footer 扩展状态行）
        this.footer.setExtensionStatus(key, text);
        this.tui.requestRender();
      },
    );
    this.themeController = new ThemeController(this.runtime, this.dialogs, this.transcript);
    this.settingsController = new SettingsController(
      this.runtime,
      this.dialogs,
      this.transcript,
      this.themeController,
      this.currentSettings,
    );
    // welcome 启动区（模型经 getter 现取——model_changed 后 refresh 生效）
    this.welcome = new WelcomeView({
      version: readPackageVersion(),
      cwd: options.cwd,
      model: () => {
        const model = this.runtime.store.currentSnapshot?.model;
        return model ? `${model.provider}/${model.id}` : undefined;
      },
      expansion,
    });
    this.headerContainer.addChild(this.welcome);
    // 已加载资源区（welcome 之下；quiet_startup 随 header 区移除）
    this.resources = new ResourcesView(this.runtime, expansion);
    this.headerContainer.addChild(this.resources);
    const editorController = new EditorController(
      this.editorRef,
      this.runtime,
      options.cwd,
      this.transcript,
      this.dialogs,
      this.themeController,
      this.settingsController,
      this.tui,
      editorContainer,
      this.foregroundTasks,
      (code) => this.quit(code),
    );
    // 前端自持导航选择器（/fork /resume /model /scoped-models）已整体迁入官方
    // bundle ui/ 段（扩展命令分发承接——dogfood）；双 Esc 与 ctrl+l 经
    // EditorController.runCommand 推命令（bundle 缺席时后端 headless 回退）。
    this.keymap = new KeymapController({
      editorRef: this.editorRef,
      runtime: this.runtime,
      dialogs: this.dialogs,
      transcript: this.transcript,
      editorController,
      foregroundTasks: this.foregroundTasks,
      toggleExpansion: () => this.toggleExpansion(),
      doubleEscapeAction: () => this.getDoubleEscapeAction(),
      toggleThinking: () => this.toggleThinkingBlocks(),
      suspend: () => this.suspendToBackground(),
      openExternalEditor: () => void this.openExternalEditor(),
      quit: (code) => this.quit(code),
    });

    // —— 接线 ——
    editorController.wire();
    this.editorController = editorController;
    this.tui.addInputListener((data) => {
      // 扩展原始终端输入拦截优先（pi onTerminalInput 对位——true 消费）
      for (const handler of this.terminalInputHandlers) {
        try {
          if (handler(data) === true) return { consume: true };
        } catch {
          // 扩展 handler 异常静默（不炸键位路由）
        }
      }
      return this.keymap.handle(data);
    });
    // 主题切换 → transcript 全量重建 + welcome/resources 重染 + 重绘
    onThemeChange(() => {
      this.welcome.refresh();
      this.resources.rebuild();
      this.transcript.rebuildAll();
      this.tui.requestRender();
    });
    // 扩展编辑器热替换：slots 整体替换完成后（refreshPackages）检测 editor:main
    this.runtime.onSlotsReplaced(() => {
      editorController.maybeSwapEditor();
      this.keymap.validateExtensionShortcuts(); // 扩展快捷键对账（restrictOverride）
    });
    // 编辑器边框色钩子：bash 模式（! 开头）→ bashMode 绿；
    // 否则 thinking 级别色（pi 同款——渲染帧现取，状态即变）
    setEditorBorderColorHook(() => {
      if (this.editorRef.current.getText().trimStart().startsWith('!')) {
        return colors.bashMode;
      }
      const level = this.runtime.store.currentSnapshot?.thinkingLevel ?? 'off';
      return thinkingBorderColor(level);
    });
  }

  private readonly editorController: EditorController;
  private readonly themeController: ThemeController;
  private readonly settingsController: SettingsController;
  private readonly keybindingDiagnostics: string[];
  /** 前端域迁移消息（构造期执行，run() 启动后透出——与键位诊断同通道）。 */
  private readonly migrationMessages: string[];
  private readonly editorRef: EditorRef;
  /** settings 缓存（run() 启动读取；/settings 可视化编辑原地更新——getter 消费方即时生效）。 */
  private readonly currentSettings: Record<string, unknown> = {};

  /** 双 Esc 导航设置（settings 派生，默认 tree——pi 同款）。 */
  private getDoubleEscapeAction(): 'fork' | 'tree' | 'none' {
    const value = this.currentSettings.doubleEscapeAction;
    return value === 'fork' || value === 'tree' || value === 'none' ? value : 'tree';
  }

  async run(): Promise<void> {
    this.tui.setFocus(this.editorRef.current);
    this.tui.start();
    // 前端设置存储与终端集成（E 流）：ui-settings 绑定 + 编辑器/清屏行为应用
    initTuiSettings(this.runtime.uiSettings);
    initTerminalIntegration({ tui: this.tui, editorRef: this.editorRef });
    // 信号与崩溃守卫（pi 对位：优雅退出/死终端应急/崩溃恢复——F 流 signals 模块）
    installSignalHandlers({
      runtime: this.runtime,
      tui: this.tui,
      quit: (code) => this.quit(code),
    });
    // tmux 键位检测（异步——警告经 transcript 提示，不阻断启动）
    void checkTmuxKeyboardSetup().then((warning) => {
      if (warning) this.transcript.addInfo(warning);
    });
    const tmuxVersionWarning = checkTmuxExtendedKeys();
    if (tmuxVersionWarning) this.transcript.addInfo(tmuxVersionWarning);
    // store → 视图：transcript 渲染 + 快照区刷新；idle 时刷新 footer 统计
    this.runtime.store.subscribe((change) => {
      if (change.area === 'transcript') this.transcript.onChange();
      if (change.area === 'snapshot') {
        this.welcome.refresh(); // 模型/会话名等变化
        updateTitle(this.runtime.store.currentSnapshot); // 终端标题联动
      }
      if (change.area === 'status') {
        updateProgress(this.runtime.store.status); // OSC 9;4 进度（设置门控）
        if (this.runtime.store.status === 'idle') void this.footer.refreshStats();
      }
      this.tui.requestRender();
    });
    // turn 结束 → 桌面通知（派生事件语义精确：agent_end 即一轮工作收尾；
    // 正文用会话名，未命名会话回退"回复完成"）
    this.runtime.bus.onDerived('turn:ended', () => {
      notifyTurnEnded('nova', this.runtime.store.currentSnapshot?.sessionName ?? '回复完成');
    });
    // 后端资源重载（/reload、/trust 后的自动重载）→ 前端刷新包 UI 贡献
    // （slots 整体重载——项目级包的渲染器/对话框/slot 命令即时进出场）；
    // 先重拉快照（/trust 翻转了 projectTrusted——trust 过滤读快照值，
    // 不先对账会把刚信任的项目包继续误杀）
    this.runtime.bus.on('session_reloaded', () => {
      void (async () => {
        await this.runtime.refreshSnapshot();
        await this.runtime.refreshPackages();
      })();
    });
    // 会话信息变更（改名/换角色/换 persona）——payload 直写角色名等，
    // 但激活工具集等派生面需全量对账（/agent 切换后 activeTools 不刷新的根因）
    this.runtime.bus.on('session_info_changed', () => {
      void this.runtime.refreshSnapshot();
    });
    // 会话内容整体替换（/resume、/new、/fork、/tree、clone、import）→
    // 全量重同步 transcript 与快照——后端持有会话单一事实源，替换后
    // 前端不重拉就会永远显示旧会话。cwd/trust 可能随切换变化，包 UI
    // 贡献（trust 过滤读快照值）也要对账
    this.runtime.bus.on('session_replaced', () => {
      void (async () => {
        await this.runtime.syncFromBackend();
        this.transcript.onChange(); // 重同步后滚到底并重绘
        void this.footer.refreshStats();
        void this.resources.refresh();
        await this.runtime.refreshPackages();
      })();
    });
    updateTitle(this.runtime.store.currentSnapshot); // 启动即设终端标题
    // 键位文件诊断（坏 JSON/未知动作/非法值）——启动即提示，不阻断
    for (const diagnostic of this.keybindingDiagnostics) {
      this.transcript.addInfo(`keybindings: ${diagnostic}`);
    }
    // 前端域迁移消息（旧位搬迁/冲突跳过）——同通道透出
    for (const message of this.migrationMessages) {
      this.transcript.addInfo(message);
    }
    try {
      await this.runtime.start();
      // settings 统一读取一次入缓存：主题 / quiet_startup / 双 Esc / first-time 判定共用
      const settings = await this.readSettings();
      Object.assign(this.currentSettings, settings);
      if (settings.quietStartup === true) {
        this.headerContainer.clear(); // 安静启动：移除 welcome 区
      }
      this.transcript.onChange(); // 历史回放（全量同步后首绘）
      this.welcome.refresh(); // 模型已同步——刷新模型行
      void this.footer.refreshStats();
      void this.resources.refresh(); // 已加载资源区（四 RPC 并发，失败静默）
      this.tui.requestRender();
      await this.themeController.init(
        typeof settings.theme === 'string' ? settings.theme : undefined,
      );
      // 主题集成（E 流）：automatic 档跟随终端配色 + 主题文件 watcher 热更新
      bindTerminalThemeSync(this.tui);
      watchThemeFiles();
      await this.editorController.setupAutocomplete();
      // 启动编排（F 流 StartupController）：信任横幅 → 压缩提示 → 命名 → resume 选择器
      await new StartupController(
        {
          runtime: this.runtime,
          transcript: this.transcript,
          // --resume：推 '/resume' 命令（bundle 包自持选择器，缺席走后端回退）
          sessions: { open: async () => this.editorController.runCommand('resume') },
        },
        { resume: this.options.resume, sessionName: this.options.sessionName },
      ).runPostStart(this.runtime.store.currentSnapshot);
      // changelog 启动提示：包版本与 settings.last_changelog_version 比对
      this.maybeNotifyNewVersion();
      // first-time-setup：无模型配置时引导 login/选模型（设过 defaultModel 或带了 --model 不弹；
      // resume 选择器等对话框开着时让路）
      if (
        !this.options.model &&
        typeof settings.defaultModel !== 'string' &&
        !this.dialogs.isActive
      ) {
        this.openFirstTimeSetup();
      }
      // initialMessage：启动即提交首条（pi 同款——first-time 引导开着则不抢焦点，跳过）
      if (this.options.initialMessage && !this.dialogs.isActive) {
        this.editorController.submitText(this.options.initialMessage, {
          images: this.options.initialImages,
        });
      }
    } catch (error) {
      this.transcript.addError(error);
    }
  }

  /** settings 读取（失败按空配置——不阻断启动）。 */
  private async readSettings(): Promise<Record<string, unknown>> {
    try {
      const result = await this.runtime.invoke('getSettings', {});
      const settings = (result as { settings?: unknown }).settings;
      return typeof settings === 'object' && settings !== null
        ? (settings as Record<string, unknown>)
        : {};
    } catch {
      return {};
    }
  }

  /** ctrl+t：thinking 块显隐切换（取反 + 持久化 + transcript 全量重建）。 */
  private toggleThinkingBlocks(): void {
    const hidden = this.currentSettings.hideThinkingBlock === true;
    this.currentSettings.hideThinkingBlock = !hidden;
    void this.runtime
      .invoke('updateSettings', { settings: { hideThinkingBlock: !hidden } })
      .catch(() => undefined);
    this.transcript.rebuildAll();
    this.tui.requestRender();
  }

  /** ctrl+o：展开-折叠全局切换（transcript/welcome/resources 三视图联动）。 */
  private toggleExpansion(): void {
    this.setExpansion(!this.expansion.expanded);
  }

  /** 展开态设值（扩展 UI 上下文 setToolsExpanded 的宿主实现——指定值幂等）。 */
  private setExpansion(expanded: boolean): void {
    if (this.expansion.expanded === expanded) return;
    this.expansion.expanded = expanded;
    this.welcome.refresh();
    this.resources.rebuild();
    this.transcript.rebuildAll();
    this.tui.requestRender();
  }

  /** 新版本启动提示（pi What's New 对位：ui-state 记录 lastSeenVersion，前进弹一次）。 */
  private maybeNotifyNewVersion(): void {
    const version = readPackageVersion();
    if (!version) return;
    const text = getWhatsNewIfNeeded(this.runtime.uiState, version);
    if (text) this.transcript.addInfo(text);
  }

  /** 终端标题（OSC 0）已迁 controllers/terminal.ts 的 updateTitle（basename + 控制字符净化）。 */

  /** ctrl+z：挂起到后台（pi handleCtrlZ 直搬——keepAlive + SIGINT 忽略 + SIGCONT 恢复）。 */
  private suspendToBackground(): void {
    if (process.platform === 'win32') {
      this.transcript.addInfo('Windows 不支持挂起（Ctrl+Z）');
      return;
    }
    // 挂起期间保持事件循环（否则 fg 恢复前进程可能因无 ref 句柄退出）
    const keepAlive = setInterval(() => {}, 2 ** 30);
    // 挂起期间忽略 SIGINT（终端里的 ctrl+c 不杀后台进程）
    const ignoreSigint = () => {};
    process.on('SIGINT', ignoreSigint);
    process.once('SIGCONT', () => {
      clearInterval(keepAlive);
      process.removeListener('SIGINT', ignoreSigint);
      this.tui.start();
      this.tui.requestRender(true);
    });
    try {
      this.tui.stop();
      process.kill(0, 'SIGTSTP'); // 整个进程组挂起
    } catch {
      clearInterval(keepAlive);
      process.removeListener('SIGINT', ignoreSigint);
      this.tui.start();
    }
  }

  /** ctrl+g：外部编辑器编辑草稿（pi openExternalEditor 直搬——退出码 0 回写）。 */
  private async openExternalEditor(): Promise<void> {
    const editorCmd =
      (typeof this.currentSettings.external_editor === 'string' &&
        this.currentSettings.external_editor) ||
      process.env.VISUAL ||
      process.env.EDITOR ||
      'vi';
    const currentText = this.editorRef.current.getText();
    const tmpFile = join(tmpdir(), `nova-editor-${Date.now()}.md`);
    try {
      writeFileSync(tmpFile, currentText, 'utf-8');
      this.tui.stop(); // 让位终端
      process.stdout.write(`${editorCmd} 已启动——退出编辑器后返回 nova。\n`);
      const [editor, ...editorArgs] = editorCmd.split(' ');
      const status = await new Promise<number | null>((resolve) => {
        const child = spawn(editor!, [...editorArgs, tmpFile], {
          stdio: 'inherit',
          shell: process.platform === 'win32',
        });
        child.on('error', () => resolve(null));
        child.on('close', (code) => resolve(code));
      });
      if (status === 0) {
        this.editorRef.current.setText(
          readFileSync(tmpFile, 'utf-8').replace(/\n$/, ''),
        );
      }
    } finally {
      try {
        unlinkSync(tmpFile);
      } catch {
        // 清理失败无碍
      }
      this.tui.start();
      this.tui.requestRender(true); // 外部编辑器用过 alternate screen——全量重绘
    }
  }

  /** first-time 引导：登录 provider / 选择默认模型 / 跳过（本地框）。 */
  private openFirstTimeSetup(): void {
    const selector = new SearchableSelector(
      '欢迎使用 nova —— 先配置模型',
      [
        { value: 'login', label: '登录 provider', description: 'OAuth 授权（Kimi 等）' },
        { value: 'model', label: '选择默认模型', description: '从已配置的模型清单选择' },
        { value: 'skip', label: '跳过', description: '稍后用 /login 或 /model 配置' },
      ],
      {
        onSelect: (value) => {
          this.dialogs.restoreLocal();
          if (value === 'login') this.editorController.runSlashCommand('/login');
          if (value === 'model') this.editorController.runCommand('model');
        },
        onCancel: () => this.dialogs.restoreLocal(),
      },
      { placeholder: '↑↓ 选择，Enter 确认' },
    );
    this.dialogs.showLocal(selector, selector);
  }

  private quit(code: number): void {
    clearTerminalProgress(); // OSC 9;4 进度清除（退出不残留任务栏指示）
    void this.runtime.stop().catch(() => undefined);
    this.tui.stop();
    // 退出提示恢复命令（pi 打印 resume 命令对位）
    process.stdout.write(`恢复会话：nova --continue\n`);
    process.exit(code);
  }
}
