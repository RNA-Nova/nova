/**
 * Nova TUI 主应用。
 *
 * 架构：Node.js 主进程（pi-tui）+ Python 子进程（nova_harness JSON-RPC）。
 */

import {
  Editor,
  SelectList,
  type SelectItem,
  CombinedAutocompleteProvider,
  type SlashCommand,
} from '@earendil-works/pi-tui';

import type { AgentEvent, UIRequest } from './rpc-client.js';
import { NovaRpcClient } from './rpc-client.js';
import { createTUIState, type TUIState } from './state.js';
import { FooterComponent } from './components/footer.js';
import { StatusMessageComponent } from './components/status-message.js';
import { WelcomeComponent } from './components/welcome.js';
import { EditorKeyboardController } from './controllers/editor-keyboard.js';
import { setColors, defaultColors } from './theme/colors.js';
import { createEditorTheme } from './theme/pi-tui-theme.js';
import { EventHandlerController } from './controllers/event-handler.js';
import { StreamingUIController } from './controllers/streaming-ui.js';
import { TranscriptController, type HistoryMessage } from './controllers/transcript.js';

export interface NovaTUIOptions {
  workDir: string;
  version: string;
  pythonPath?: string;
  serverModule?: string;
  sessionFlag?: string | undefined;
  continueLast?: boolean;
  model?: string | undefined;
  agentName?: string | undefined;
}

export class NovaTUI {
  private state: TUIState;
  private client: NovaRpcClient;
  private footer: FooterComponent;
  private editor: Editor;
  private shuttingDown = false;

  // Controllers
  private transcriptCtrl: TranscriptController;
  private streamingCtrl: StreamingUIController;
  private eventHandler: EventHandlerController;
  private editorKeyboard: EditorKeyboardController;

  public onExit?: (exitCode?: number) => Promise<void>;

  constructor(private readonly options: NovaTUIOptions) {
    setColors(defaultColors);
    this.state = createTUIState(options.workDir, options.version);
    this.footer = new FooterComponent(this.state.appState);
    this.editor = new Editor(this.state.ui, createEditorTheme(defaultColors), { paddingX: 2 });

    this.client = new NovaRpcClient(
      options.pythonPath || 'python',
      options.serverModule || 'nova_harness.rpc',
    );
    this._setupAutocomplete();

    // 初始化 Controllers
    this.transcriptCtrl = new TranscriptController(this.state);
    this.streamingCtrl = new StreamingUIController(this.state, this.transcriptCtrl);
    this.eventHandler = new EventHandlerController(this.streamingCtrl, this.transcriptCtrl);
    this.editorKeyboard = new EditorKeyboardController(this.editor, this.footer, {
      sendUserInput: (text) => this._sendUserInput(text),
      abortStream: () => this._abortStream(),
      stop: (code) => this.stop(code ?? 0),
      isStreaming: () => this.streamingCtrl.isStreaming,
      requestRender: () => this.state.ui.requestRender(),
      onSlashInput: (text) => this._handleSlashInput(text),
      onToggleToolExpand: () => this.streamingCtrl.toggleExpanded(),
    });

    this._buildLayout();
    this._renderWelcome();
    this._startContextPolling();
  }

  private _contextPollTimer: ReturnType<typeof setInterval> | undefined;

  private _startContextPolling(): void {
    this._contextPollTimer = setInterval(() => {
      void this._pollContextUsage();
    }, 3000);
  }

  private async _pollContextUsage(): Promise<void> {
    try {
      const usage = (await this.client.call('getContextUsage', {})) as {
        tokens?: number;
        context_window?: number;
        percent?: number;
      };
      if (usage.tokens !== undefined) {
        this.state.appState.contextTokens = usage.tokens;
      }
      if (usage.context_window !== undefined) {
        this.state.appState.maxContextTokens = usage.context_window;
      }
      if (usage.percent !== undefined) {
        this.state.appState.contextUsage = usage.percent / 100;
      }
      this.state.ui.requestRender();
    } catch {
      // ignore
    }
  }

  private _stopContextPolling(): void {
    if (this._contextPollTimer) {
      clearInterval(this._contextPollTimer);
      this._contextPollTimer = undefined;
    }
  }

  // ------------------------------------------------------------------
  // 生命周期
  // ------------------------------------------------------------------
  async start(): Promise<void> {
    await this.client.start();
    this.client.onEvent((evt) => this._handleAgentEvent(evt));
    this.client.onUIRequest((req) => this._handleUIRequest(req));
    this.client.onClose(() => {
      if (!this.shuttingDown) {
        this.transcriptCtrl.showStatus('Python server disconnected', '#e06c75');
      }
    });

    this.editorKeyboard.install();

    try {
      await this.client.call('initialize', {});
    } catch (e) {
      this.transcriptCtrl.showError(
        `RPC initialize failed: ${e instanceof Error ? e.message : String(e)}`,
      );
      // 继续启动 UI，让用户看到错误信息
      this.state.ui.start();
      return;
    }

    // Start UI early so that interactive session selection can render.
    this.state.ui.start();

    let sessionFlag: string | undefined = this.options.sessionFlag;
    if (sessionFlag === '') {
      const selected = await this._promptSessionSelection();
      sessionFlag = selected ?? undefined;
    }

    try {
      const createParams: Record<string, unknown> = {
        cwd: this.options.workDir,
        sessionFlag,
        continueLast: this.options.continueLast,
      };
      if (this.options.model) {
        createParams.model = this.options.model;
      }
      if (this.options.agentName) {
        createParams.agentName = this.options.agentName;
      }
      const session = await this.client.call('createSession', createParams) as {
        session_id: string;
        session_name?: string;
        resumed?: boolean;
      };
      this.state.appState.sessionId = session.session_id;
      this.state.appState.agentName = this.options.agentName || 'coding_agent';
      if (this.options.model) {
        this.state.appState.model = this.options.model;
      }

      if (session.resumed) {
        this.transcriptCtrl.clear();
        await this._loadSessionHistory();
        this.transcriptCtrl.showStatus(`Resumed session: ${session.session_id}`);
      } else {
        this.transcriptCtrl.showStatus(`Started session: ${session.session_id}`);
      }
    } catch (e) {
      this.transcriptCtrl.showError(
        `Failed to create session: ${e instanceof Error ? e.message : String(e)}`,
      );
    }

    // 注册信号处理器，确保终端状态在异常退出时也能恢复
    process.once('SIGTERM', () => {
      void this.stop(143);
    });
    process.once('SIGINT', () => {
      void this.stop(130);
    });
  }

  async stop(exitCode = 0): Promise<void> {
    if (this.shuttingDown) return;
    this.shuttingDown = true;
    this._stopContextPolling();
    this.state.ui.stop();
    await this.client.stop();
    await this.onExit?.(exitCode);
  }

  // ------------------------------------------------------------------
  // 布局
  // ------------------------------------------------------------------
  private _buildLayout(): void {
    const { ui, transcriptContainer, activityContainer, editorContainer, footerContainer } = this.state;
    ui.clear();
    ui.addChild(transcriptContainer);
    ui.addChild(activityContainer);
    ui.addChild(editorContainer);
    ui.addChild(footerContainer);

    editorContainer.addChild(this.editor);
    footerContainer.addChild(this.footer);
    ui.setFocus(this.editor);
  }

  private _renderWelcome(): void {
    const welcome = new WelcomeComponent(this.options.version);
    this.state.transcriptContainer.addChild(welcome);
  }

  // ------------------------------------------------------------------
  // Session selection
  // ------------------------------------------------------------------
  private async _promptSessionSelection(): Promise<string | null> {
    type SessionInfo = { id: string; name?: string; modified?: number };
    let sessions: SessionInfo[] = [];
    try {
      sessions = (await this.client.call('listSessions', { cwd: this.options.workDir })) as SessionInfo[];
    } catch {
      return null;
    }

    if (sessions.length === 0) {
      this.transcriptCtrl.showStatus('No existing sessions — creating a new one', '#e5c07b');
      return null;
    }

    return new Promise((resolve) => {
      const formatTime = (ts?: number): string => {
        if (!ts) return '';
        const d = new Date(ts * 1000);
        return d.toLocaleString('zh-CN', {
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        });
      };

      const items: SelectItem[] = [
        { value: '', label: 'Create new session', description: 'start a fresh conversation' },
        ...sessions.map((s) => ({
          value: s.id,
          label: s.name || s.id,
          description: formatTime(s.modified),
        })),
      ];

      const theme = createEditorTheme(defaultColors);
      const selectList = new SelectList(items, Math.min(10, items.length), theme.selectList);

      selectList.onSelect = (item) => {
        this.state.activityContainer.removeChild(selectList);
        this.state.ui.setFocus(this.editor);
        this.state.ui.requestRender();
        resolve(item.value || null);
      };

      selectList.onCancel = () => {
        this.state.activityContainer.removeChild(selectList);
        this.state.ui.setFocus(this.editor);
        this.state.ui.requestRender();
        resolve(null);
      };

      this.state.activityContainer.addChild(selectList);
      this.state.ui.setFocus(selectList);
      this.state.ui.requestRender();
    });
  }

  // ------------------------------------------------------------------
  // 用户输入
  // ------------------------------------------------------------------
  private async _sendUserInput(text: string): Promise<void> {
    if (this.streamingCtrl.isStreaming) {
      this.transcriptCtrl.showStatus('Waiting for current response...', '#e5c07b');
      return;
    }

    // Handle slash commands
    if (text.startsWith('/agent ')) {
      const name = text.slice(7).trim();
      if (!name) {
        this.transcriptCtrl.showStatus('Usage: /agent <name>', '#e5c07b');
        return;
      }
      try {
        const result = await this.client.call('changeAgent', { name }) as {
          agent_name?: string;
          available_tools?: string[];
        };
        this.state.appState.agentName = name;
        this.transcriptCtrl.showStatus(
          `Switched to agent: ${name} (tools: ${(result.available_tools || []).join(', ')})`,
          '#98c379',
        );
      } catch (e) {
        this.transcriptCtrl.showError(
          `Switch agent failed: ${e instanceof Error ? e.message : String(e)}`,
        );
      }
      return;
    }

    if (text === '/agents') {
      try {
        const agents = await this.client.call('listAgents', {}) as Array<{ name: string }>;
        const names = agents.map((a) => a.name).join(', ');
        this.transcriptCtrl.showStatus(`Installed agents: ${names}`, '#61afef');
      } catch (e) {
        this.transcriptCtrl.showError(
          `List agents failed: ${e instanceof Error ? e.message : String(e)}`,
        );
      }
      return;
    }

    this.transcriptCtrl.appendEntry({
      id: this.transcriptCtrl.nextId(),
      kind: 'user',
      content: text,
    });

    this.streamingCtrl.beginWaiting();

    try {
      await this.client.call('prompt', { text });
    } catch (e) {
      this.transcriptCtrl.showError(
        `Send failed: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  }

  private async _abortStream(): Promise<void> {
    // Visual cancel first — stop rendering immediately on the frontend
    this.streamingCtrl.cancel();
    this.transcriptCtrl.showStatus('Cancelled', '#e5c07b');
    try {
      await this.client.call('abort', {});
    } catch {
      // ignore
    }
  }

  // ------------------------------------------------------------------
  // Session history
  // ------------------------------------------------------------------
  private async _loadSessionHistory(): Promise<void> {
    try {
      const response = (await this.client.call('getSessionMessages', { limit: 50 })) as {
        messages?: HistoryMessage[];
      };
      const messages = response.messages || [];
      if (messages.length > 0) {
        this.transcriptCtrl.loadHistory(messages);
      }
    } catch (e) {
      this.transcriptCtrl.showError(
        `Load history failed: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  }

  // ------------------------------------------------------------------
  // Autocomplete / slash commands
  // ------------------------------------------------------------------
  private _setupAutocomplete(): void {
    const agentCommand: SlashCommand = {
      name: 'agent',
      description: 'switch agent',
      argumentHint: '<name>',
      getArgumentCompletions: async () => {
        try {
          const agents = (await this.client.call('listAgents', {})) as Array<{ name: string }>;
          return agents.map((a) => ({ value: a.name, label: a.name }));
        } catch {
          return null;
        }
      },
    };

    const slashCommands: SlashCommand[] = [
      agentCommand,
      { name: 'agents', description: 'list available agents' },
    ];

    const provider = new CombinedAutocompleteProvider(slashCommands, this.options.workDir, null);

    if (typeof (this.editor as any).setAutocompleteProvider === 'function') {
      (this.editor as any).setAutocompleteProvider(provider);
    }
    if (typeof (this.editor as any).setAutocompleteMaxVisible === 'function') {
      (this.editor as any).setAutocompleteMaxVisible(8);
    }
  }

  // ------------------------------------------------------------------
  // Slash command hint
  // ------------------------------------------------------------------
  private _handleSlashInput(text: string): void {
    if (!text.startsWith('/')) {
      this.footer.setCommandHint(null);
      return;
    }
    const token = text.slice(1).trim().split(/\s+/)[0] ?? '';
    const commands = [
      { name: 'agent', desc: 'switch agent' },
      { name: 'agents', desc: 'list agents' },
    ];
    const matches = commands.filter((c) => c.name.startsWith(token));
    if (matches.length === 0) {
      this.footer.setCommandHint(null);
    } else {
      const hint = matches.map((m) => `/${m.name} — ${m.desc}`).join('  ');
      this.footer.setCommandHint(hint);
    }
    this.state.ui.requestRender();
  }

  // ------------------------------------------------------------------
  // Agent 事件
  // ------------------------------------------------------------------
  private _handleAgentEvent(evt: AgentEvent): void {
    try {
      this.eventHandler.handleEvent(evt);
    } catch (e) {
      this.transcriptCtrl.showError(
        `Event handling failed: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
    if (evt.type === 'agent_end' || evt.type === 'turn_end') {
      void this._pollContextUsage();
    }
  }

  // ------------------------------------------------------------------
  // UI 原语请求（由后端扩展触发）
  // ------------------------------------------------------------------
  private async _handleUIRequest(req: UIRequest): Promise<void> {
    const { id, method } = req;
    try {
      switch (method) {
        case 'select': {
          const options = (req.options as string[]) || [];
          const title = (req.title as string) || '';
          const selected = await this._promptSelection(title, options);
          this.client.sendUIResponse(id, selected);
          break;
        }
        case 'confirm': {
          const title = (req.title as string) || '';
          const message = (req.message as string) || '';
          const confirmed = await this._promptConfirm(title, message);
          this.client.sendUIResponse(id, confirmed);
          break;
        }
        case 'input': {
          // TODO: 实现真正的输入弹窗；目前返回空字符串避免阻塞。
          this.client.sendUIResponse(id, '');
          break;
        }
        case 'notify': {
          const message = (req.message as string) || '';
          const type = (req.type as string) || 'info';
          const color = type === 'error' ? '#e06c75' : type === 'warning' ? '#e5c07b' : '#98c379';
          this.transcriptCtrl.showStatus(message, color);
          break;
        }
        case 'setStatus': {
          const text = (req.text as string | undefined) ?? '';
          this.transcriptCtrl.showStatus(text, '#61afef');
          break;
        }
        case 'setWorkingMessage': {
          const message = (req.message as string) || '';
          this.transcriptCtrl.showStatus(message, '#61afef');
          break;
        }
        case 'setWorkingVisible': {
          // 目前无独立 working indicator，忽略可见性变化。
          break;
        }
        default:
          // 未知方法默认返回 null
          this.client.sendUIResponse(id, null);
      }
    } catch (e) {
      this.transcriptCtrl.showError(
        `UI request failed: ${e instanceof Error ? e.message : String(e)}`,
      );
      this.client.sendUIResponse(id, null);
    }
  }

  private async _promptSelection(title: string, options: string[]): Promise<string | null> {
    return new Promise((resolve) => {
      const theme = createEditorTheme(defaultColors);
      const items: SelectItem[] = options.map((label) => ({ value: label, label }));
      const selectList = new SelectList(items, Math.min(10, items.length), theme.selectList);
      selectList.onSelect = (item) => {
        this.state.activityContainer.removeChild(selectList);
        this.state.ui.setFocus(this.editor);
        this.state.ui.requestRender();
        resolve((item.value as string) || null);
      };
      selectList.onCancel = () => {
        this.state.activityContainer.removeChild(selectList);
        this.state.ui.setFocus(this.editor);
        this.state.ui.requestRender();
        resolve(null);
      };
      this.transcriptCtrl.showStatus(title, '#e5c07b');
      this.state.activityContainer.addChild(selectList);
      this.state.ui.setFocus(selectList);
      this.state.ui.requestRender();
    });
  }

  private async _promptConfirm(title: string, message: string): Promise<boolean> {
    const selected = await this._promptSelection(`${title}\n${message}`, ['Yes', 'No']);
    return selected === 'Yes';
  }
}
