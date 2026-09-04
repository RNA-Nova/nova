/**
 * EditorController：编辑器接线编排。
 *
 * 职责：
 * - onSubmit 分发：``!`` 前缀走 bash 用户工具、
 *   ``/`` 前缀走可取消命令（cancelRequest 句柄注册到 dialogs）、
 *   其余走普通 prompt；
 * - slash 命令补全安装（命令表经 RPC 拉取）；
 * - 扩展编辑器热替换（``editor:main`` slot 注册工厂时）：迁移文本、
 *   重接线、槽位换人——controllers 经 ``EditorRef`` 盒子共享当前实例。
 *
 * 纪律：只做接线编排——用户消息渲染不做本地预绘（经事件流回 store 后
 * 由 transcript 渲染）；命令反馈经 transcript 的本地消息出口。
 */

import type { NovaUIRuntime } from 'nova-tui';
import { commandSlot, editorSlot, guardComponentLineWidth, type EditorFactory } from 'nova-tui';
import type { ImageContent } from '../../../protocol/nova-wire.gen.js';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { userFrontendDir } from '../../../paths.js';
import {
  CombinedAutocompleteProvider,
  getKeybindings,
  type AutocompleteProvider,
  type Editor,
  type EditorComponent,
  type SlashCommand,
  type TUI,
} from '@earendil-works/pi-tui';
import type { Container } from '@earendil-works/pi-tui';

/**
 * 组合补全 provider（扩展源在前、基线在后）：建议条目合并去重（按 label 首见），
 * applyCompletion 按条目来源路由回原 provider。
 */
class CompositeAutocompleteProvider implements AutocompleteProvider {
  private readonly origin = new WeakMap<object, AutocompleteProvider>();

  constructor(private readonly providers: AutocompleteProvider[]) {}

  get triggerCharacters(): string[] {
    return [
      ...new Set(this.providers.flatMap((provider) => provider.triggerCharacters ?? [])),
    ];
  }

  async getSuggestions(
    lines: string[],
    cursorLine: number,
    cursorCol: number,
    options: { signal: AbortSignal; force?: boolean },
  ) {
    const items: Array<Record<string, unknown>> = [];
    const seen = new Set<string>();
    for (const provider of this.providers) {
      let result: Awaited<ReturnType<AutocompleteProvider['getSuggestions']>>;
      try {
        result = await provider.getSuggestions(lines, cursorLine, cursorCol, options);
      } catch {
        continue; // 单个 provider 异常不拖垮补全
      }
      if (!result) continue;
      for (const item of result.items) {
        const label = String((item as { label?: unknown }).label ?? '');
        if (seen.has(label)) continue;
        seen.add(label);
        this.origin.set(item as object, provider);
        items.push(item as unknown as Record<string, unknown>);
      }
      if (result.prefix !== undefined) {
        // 前缀以首个给出建议的 provider 为准（组合语义与 pi-tui 组合器一致）
        return { items, prefix: result.prefix } as never;
      }
    }
    return { items } as never;
  }

  applyCompletion(
    lines: string[],
    cursorLine: number,
    cursorCol: number,
    item: never,
    prefix: string,
  ) {
    const provider = this.origin.get(item as object) ?? this.providers[this.providers.length - 1];
    return provider.applyCompletion(lines, cursorLine, cursorCol, item, prefix);
  }
}

import { colors, editorTheme } from '../themes/index.js';
import { buildCommandDirectory } from '../commands/directory.js';
import { HelpViewer } from '../components/dialogs/help-viewer.js';
import { readClipboardText, saveClipboardImageToTemp } from '../utils/clipboard.js';
import { exportSessionHtml } from './export.js';
import { shareSession } from './share.js';
import { renderChangelogEntry } from '../utils/changelog.js';
import type { DialogController } from './dialogs.js';
import type { ForegroundTasks } from './foreground.js';
import type { SettingsController } from './settings.js';
import type { ThemeController } from './theme.js';
import type { TranscriptController } from './transcript.js';

/**
 * 编辑器引用盒：扩展编辑器热替换的共享通道——controllers 持有盒子，
 * 每次用 ``ref.current`` 现取（替换自动对全体生效）。
 */
export interface EditorRef {
  current: EditorComponent;
}

export class EditorController {
  /** 当前编辑器来源工厂（热替换幂等判定——同工厂不重复换）。 */
  private activeFactory: EditorFactory | undefined;

  constructor(
    private readonly editorRef: EditorRef,
    private readonly runtime: NovaUIRuntime,
    private readonly cwd: string,
    private readonly transcript: TranscriptController,
    private readonly dialogs: DialogController,
    private readonly theme: ThemeController,
    private readonly settings: SettingsController,
    private readonly tui: TUI,
    private readonly editorContainer: Container,
    private readonly foregroundTasks: ForegroundTasks,
    private readonly quit: (code: number) => void,
  ) {}

  /** 双 Esc 导航入口（keymap 调用）：推命令（bundle 包自持 UI > 后端 slash 回退）。 */
  openNavigation(action: 'tree' | 'fork'): void {
    this.runCommand(action);
  }

  /**
   * 推命令（经 isCommandEnabled 门控；扩展命令 slot 优先，缺席走后端 slash 回退）：
   * /tree /fork /resume /model /scoped-models 等包自持命令 UI 的统一入口——
   * 双 Esc 导航、ctrl+l、first-time 引导、--resume 启动共用
   * （与编辑器输入的命令分发同判决，只是不带参数）。
   */
  runCommand(name: string): void {
    // 与编辑器输入分发同判：agent.yaml commands 允许集/settings 排除集
    // 对程序化入口（热键、引导）同样生效，不允许绕过白名单。
    if (!this.isCommandEnabled(name)) {
      this.transcript.addInfo(`命令 /${name} 已被当前 agent 配置或用户设置禁用`);
      return;
    }
    const extensionCommand = this.runtime.slots.resolve<string, unknown>(commandSlot(name));
    if (extensionCommand !== undefined) {
      void Promise.resolve(extensionCommand('')).catch((error) =>
        this.transcript.addError(error),
      );
      return;
    }
    this.runSlashCommand(`/${name}`);
  }

  /** 命令是否可用（快照的 allowedCommands/disabledCommands 过滤 Node 扩展命令）。 */
  private isCommandEnabled(name: string): boolean {
    const snapshot = this.runtime.store.currentSnapshot;
    const allowed = snapshot?.allowedCommands;
    const disabled = snapshot?.disabledCommands;
    if (Array.isArray(allowed) && allowed.length > 0 && !allowed.includes(name)) {
      return false;
    }
    if (Array.isArray(disabled) && disabled.includes(name)) {
      return false;
    }
    return true;
  }

  /** 接线 onSubmit（热替换后重调——新实例重新挂回调）。 */
  wire(): void {
    this.editorRef.current.onSubmit = (text) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      this.editorRef.current.addToHistory?.(trimmed);
      this.submitText(trimmed);
    };
  }

  /**
   * 提交分发（编辑器 onSubmit 与 alt+enter 等键位提交共用）：
   * - ``!!cmd``：bash 不进 LLM 上下文（exclude_from_context）；
   * - ``!cmd``：bash 用户工具（输出进上下文）；
   * - /theme /settings：前端本地命令；
   * - 其余 slash：可取消调用（流式中后端拦扩展命令立即执行——安全）；
   * - 普通文本：working 时必须带 streamingBehavior（后端否则抛错）——
   *   缺省 steer（插入当前 turn），``followUp: true`` 排队等 run 结束。
   */
  submitText(trimmed: string, options?: { followUp?: boolean; images?: ImageContent[] }): void {
    if (trimmed.startsWith('!!')) {
      void this.runtime
        .invokeUserTool('bash', { command: trimmed.slice(2), exclude_from_context: true })
        .catch((error) => this.transcript.addError(error));
      return;
    }
    if (trimmed.startsWith('!')) {
      void this.runtime
        .invokeUserTool('bash', { command: trimmed.slice(1) })
        .catch((error) => this.transcript.addError(error));
      return;
    }
    if (trimmed === '/theme') {
      this.theme.openSelector();
      return;
    }
    // /help：命令目录查看器（三源合并含本地命令——宿主本地实现，
    // 遮蔽后端 26 项版（它只见后端注册表，没有本地/包 slot 命令）
    if (trimmed === '/help') {
      void this.openHelpViewer().catch((error) => this.transcript.addError(error));
      return;
    }
    if (trimmed === '/settings') {
      this.settings.openSelector();
      return;
    }
    // 前端自持导航选择器（/tree /fork /resume /model /scoped-models）已整体迁入
    // 官方 bundle ui/ 段（扩展命令分发承接——dogfood）；后端同名命令保留 headless 回退。
    // /share：分享会话为 secret gist（前端自持——HTML 导出在 Node 层）
    if (trimmed === '/share') {
      void shareSession(this.runtime, this.transcript, this.foregroundTasks).catch((error) =>
        this.transcript.addError(error),
      );
      return;
    }
    // /quit：退出（ctrl+c 双击/ctrl+d 空退的命令形态）
    if (trimmed === '/quit') {
      this.quit(0);
      return;
    }
    // /changelog：渲染仓库更新日志（Unreleased 优先，否则最新版本段）
    if (trimmed === '/changelog') {
      const markdown = renderChangelogEntry();
      if (markdown) this.transcript.addMarkdown(markdown);
      else this.transcript.addInfo('未找到 CHANGELOG');
      return;
    }
    // /copy：复制最后一条 assistant 消息（ctrl+x 键位同路径）
    if (trimmed === '/copy') {
      void this.transcript.copyLastAssistantMessage();
      return;
    }
    // /hotkeys：键位表展示（键位系统现状全览——含用户重绑定后的解析结果）
    if (trimmed === '/hotkeys') {
      this.showHotkeys();
      return;
    }
    // /debug：状态 dump 到文件（前端调试出口——快照 + 条目统计）
    if (trimmed === '/debug') {
      this.writeDebugLog();
      return;
    }
    if (trimmed.startsWith('/')) {
      // /export 分叉：.jsonl 走后端命令（JSONL 复制）；无参数或 .html 走前端 HTML 导出
      if (trimmed === '/export' || trimmed.startsWith('/export ')) {
        const exportPath = trimmed === '/export' ? undefined : trimmed.slice(8).trim();
        if (exportPath === undefined || !exportPath.endsWith('.jsonl')) {
          void exportSessionHtml(this.runtime, this.transcript, this.cwd, exportPath).catch(
            (error) => this.transcript.addError(error),
          );
          return;
        }
      }
      // Node 扩展命令优先（registerCommand——统一命令表的 Node 源）
      const spaceIndex = trimmed.indexOf(' ');
      const name = spaceIndex === -1 ? trimmed.slice(1) : trimmed.slice(1, spaceIndex);
      // 命令过滤（快照透出：agent.yaml commands 允许集 + settings 排除集）
      if (!this.isCommandEnabled(name)) {
        this.transcript.addInfo(`命令 /${name} 已被当前 agent 配置或用户设置禁用`);
        return;
      }
      const extensionCommand = this.runtime.slots.resolve<string, unknown>(
        commandSlot(name),
      );
      if (extensionCommand !== undefined) {
        const args = spaceIndex === -1 ? '' : trimmed.slice(spaceIndex + 1);
        void Promise.resolve(extensionCommand(args)).catch((error) =>
          this.transcript.addError(error),
        );
        return;
      }
      // 后端 slash 命令：可取消调用（OAuth 登录等长命令的 Esc 入口）。
      // 普通对话不走这里——run 的取消语义是 abort（领域清理），不是取消调用
      this.runSlashCommand(trimmed);
      return;
    }
    // user 条目经事件流回 store 后由 transcript 渲染，不做本地预绘
    const working = this.runtime.store.status === 'working';
    const hasImages = options?.images !== undefined && options.images.length > 0;
    void this.runtime
      .prompt(
        trimmed,
        working || hasImages
          ? {
              ...(working
                ? { streamingBehavior: options?.followUp ? ('followUp' as const) : ('steer' as const) }
                : {}),
              ...(hasImages ? { images: options.images } : {}),
            }
          : undefined,
      )
      .catch((error) => this.transcript.addError(error));
  }

  /** /help：命令目录查看器（三源合并——与补全目录同一事实源）。 */
  private async openHelpViewer(): Promise<void> {
    const entries = await buildCommandDirectory(this.runtime, (name) =>
      this.isCommandEnabled(name),
    );
    await this.dialogs.customLocal((_env, done) => new HelpViewer(entries, () => done()));
  }

  /**
   * 队列还原：
   * 清空 steering/follow-up 队列，内容按时间序填回编辑器（现有草稿附后）。
   * alt+↑ 直接调用；Esc 中断 run 前先调用（排队内容不丢）。
   */
  async dequeueToEditor(): Promise<void> {
    const { steering, followUp } = await this.runtime.clearQueue();
    const queued = [...steering, ...followUp];
    if (queued.length === 0) return;
    const current = this.editorRef.current.getText().trim();
    this.editorRef.current.setText([...queued, current].filter(Boolean).join('\n'));
  }

  /**
   * 扩展编辑器热替换（refreshPackages 后由装配根触发）：
   * ``editor:main`` slot 有注册工厂且非当前来源时——建厂、迁移文本、
   * 重接线、槽位换人（对话框开着时只换 ref，关框恢复即新编辑器）。
   */
  maybeSwapEditor(): void {
    const factory = this.runtime.slots.resolve<unknown, unknown>(editorSlot()) as
      | EditorFactory
      | undefined;
    if (factory === undefined || factory === this.activeFactory) return;

    const candidate = factory({
      tui: this.tui,
      theme: editorTheme,
      keybindings: getKeybindings(),
    });
    // EditorComponent 契约防御（必需的三个方法缺一不可）
    const editor = candidate as EditorComponent;
    if (
      editor === null ||
      typeof editor !== 'object' ||
      typeof editor.getText !== 'function' ||
      typeof editor.setText !== 'function' ||
      typeof editor.handleInput !== 'function'
    ) {
      this.transcript.addError(
        new Error('扩展编辑器不满足 EditorComponent 契约（缺 getText/setText/handleInput）'),
      );
      return;
    }

    const previous = this.editorRef.current;
    editor.setText(previous.getText()); // 文本迁移（光标/历史不迁——接口无光标通道）
    this.activeFactory = factory;
    this.editorRef.current = editor;
    this.wire();
    // 槽位：无对话框时直接换人（有框则关框恢复时经 ref 自动用新编辑器）
    // 行宽防线：扩展编辑器超宽行不得崩掉整个 TUI
    if (!this.dialogs.isActive) {
      this.editorContainer.clear();
      this.editorContainer.addChild(guardComponentLineWidth(editor));
      this.tui.setFocus(editor);
      this.tui.requestRender();
    }
    this.transcript.addInfo('扩展编辑器已启用');
  }

  /**
   * 执行 slash 命令（可取消调用 + 取消句柄注册到 dialogs）。
   * 编辑器提交 / 双 Esc 导航 / first-time 引导共用此路径。
   */
  runSlashCommand(command: string): void {
    const { promise, cancel } = this.runtime.promptCancellable(command);
    this.dialogs.setPendingCommandCancel(cancel);
    promise
      .catch((error: unknown) => {
        // AbortError（用户取消）：前端即时反馈——取消由前端发起（Esc），且
        // 后端无法可靠自报（任务取消后 send_message 的 await 不可靠）。
        if (error instanceof Error && error.name === 'AbortError') {
          this.transcript.addInfo('已取消');
          return;
        }
        this.transcript.addError(error);
      })
      .finally(() => {
        this.dialogs.clearPendingCommand();
      });
  }

  /**
   * Ctrl+V 剪贴板粘贴：
   * 图片 → 写临时文件、路径文本进编辑器（提交纯文本上送，LLM 经 read
   * 工具读图）；无图片 → 退化为剪贴板文本插入。读取失败静默。
   */
  async pasteFromClipboard(): Promise<void> {
    const imagePath = await saveClipboardImageToTemp();
    if (imagePath) {
      this.editorRef.current.insertTextAtCursor?.(imagePath);
      return;
    }
    const text = await readClipboardText();
    if (text) this.editorRef.current.insertTextAtCursor?.(text);
  }

  /** /hotkeys：键位表全量展示（解析后生效值——含用户 keybindings.json 重绑定）。 */
  private showHotkeys(): void {
    const kb = getKeybindings();
    const resolved = kb.getResolvedBindings();
    const lines = Object.entries(resolved).map(([id, keys]) => {
      const definition = kb.getDefinition(id as never);
      const description = definition?.description ?? '';
      const keyList = (Array.isArray(keys) ? keys : [keys]).filter(Boolean);
      const keyText = keyList.length > 0 ? keyList.join('/') : '(未绑定)';
      return `  ${keyText.padEnd(24)} ${colors.dim(`${id}  ${description}`)}`;
    });
    this.transcript.addInfo(
      `键位表（~/.nova/agent/frontend/tui/keybindings.json 或项目 .nova/frontend/tui/keybindings.json 可重映射）：\n${lines.join('\n')}`,
    );
  }

  /** /debug：镜像状态 dump 到 frontend/tui/debug/debug-<ts>.log。 */
  private writeDebugLog(): void {
    try {
      const dir = join(userFrontendDir(), 'debug');
      mkdirSync(dir, { recursive: true });
      const path = join(dir, `debug-${Date.now()}.log`);
      const snapshot = this.runtime.store.currentSnapshot;
      const entries = this.runtime.store.entries;
      const kindCounts: Record<string, number> = {};
      for (const entry of entries) {
        kindCounts[entry.kind] = (kindCounts[entry.kind] ?? 0) + 1;
      }
      const report = [
        `nova debug dump — ${new Date().toISOString()}`,
        `cwd: ${this.cwd}`,
        '',
        '== snapshot ==',
        JSON.stringify(snapshot, null, 2),
        '',
        '== entries（kind 计数）==',
        JSON.stringify(kindCounts, null, 2),
        `total: ${entries.length}`,
        '',
        '== slots 注册表 ==',
        JSON.stringify(this.runtime.slots.list(), null, 2),
      ].join('\n');
      writeFileSync(path, report, 'utf-8');
      this.transcript.addInfo(`debug 状态已写入: ${path}`);
    } catch (error) {
      this.transcript.addError(error);
    }
  }

  /** slash 命令补全（命令表经 RPC 拉取 + 前端本地命令追加）。 */
  async setupAutocomplete(): Promise<void> {
    try {
      // 三源合并去重归 commands/directory（/help 视图同源——
      // 覆盖优先级 = 分发现实：本地 > slot > 后端）
      const directory = await buildCommandDirectory(this.runtime, (name) =>
        this.isCommandEnabled(name),
      );
      // prompt/skill 与真命令外观相同但行为迥异（展开后发给 LLM vs 本地动作）——
      // 补全描述前缀标注，避免"以为敲了个本地命令"
      const commands: SlashCommand[] = directory.map((entry) => ({
        name: entry.name,
        description:
          entry.kind === 'prompt'
            ? `提示词模板 · ${entry.description ?? ''}`
            : entry.kind === 'skill'
              ? `技能 · ${entry.description ?? ''}`
              : entry.description,
        // slot 命令的参数补全（注册时附着函数对象）
        ...(entry.source === 'slot'
          ? {
              getArgumentCompletions: (prefix: string) => {
                const fn = this.runtime.slots.resolve<string, unknown>(
                  `command:${entry.name}` as never,
                );
                const gac = (
                  fn as {
                    getArgumentCompletions?: (
                      p: string,
                    ) =>
                      | Array<{ value: string; label: string; description?: string }>
                      | null
                      | Promise<Array<{ value: string; label: string }> | null>;
                  }
                )?.getArgumentCompletions;
                return gac ? gac(prefix) : null;
              },
            }
          : {}),
      }));
      const base = new CombinedAutocompleteProvider(commands, this.cwd);
      // 扩展补全源（autocomplete:* slot——建议排在基线之前）
      const extensionProviders: AutocompleteProvider[] = [];
      for (const { key } of this.runtime.slots.list()) {
        if (!key.startsWith('autocomplete:')) continue;
        const producer = this.runtime.slots.resolve<never, unknown>(key as never) as
          | (() => unknown)
          | undefined;
        const provider = typeof producer === 'function' ? producer() : undefined;
        if (
          provider &&
          typeof (provider as AutocompleteProvider).getSuggestions === 'function'
        ) {
          extensionProviders.push(provider as AutocompleteProvider);
        }
      }
      this.editorRef.current.setAutocompleteProvider?.(
        extensionProviders.length > 0
          ? new CompositeAutocompleteProvider([...extensionProviders, base])
          : base,
      );
    } catch {
      // 补全失败不影响主流程
    }
  }
}
