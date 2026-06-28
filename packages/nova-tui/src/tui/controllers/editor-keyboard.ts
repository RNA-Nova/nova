/**
 * 编辑器按键控制器。
 * 负责编辑器提交、快捷键（Ctrl+C / Ctrl+D）的绑定与处理。
 */

import { type Editor, matchesKey, Key } from '@earendil-works/pi-tui';

import type { FooterComponent } from '../components/footer.js';

export interface EditorKeyboardHost {
  sendUserInput(text: string): Promise<void>;
  abortStream(): Promise<void>;
  stop(exitCode?: number): Promise<void>;
  isStreaming(): boolean;
  requestRender(): void;
  onSlashInput?(text: string): void;
  onToggleToolExpand?(): void;
}

export class EditorKeyboardController {
  private pendingExit: { kind: 'ctrl-c'; timer: ReturnType<typeof setTimeout> } | null = null;
  private readonly EXIT_CONFIRM_MS = 2000;

  constructor(
    private readonly editor: Editor,
    private readonly footer: FooterComponent,
    private readonly host: EditorKeyboardHost,
  ) {}

  install(): void {
    // onSubmit 是 pi-tui Editor 原生支持的回调
    (this.editor as any).onSubmit = (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      this.editor.setText('');
      void this.host.sendUserInput(trimmed);
    };

    // pi-tui Editor 的 handleInput 在收到 Ctrl+C 时会匹配 "tui.input.copy" 并直接 return，
    // 不会触发任何回调。因此我们需要直接拦截 handleInput 来捕获 Ctrl+C 和 Ctrl+D。
    const originalHandleInput = (this.editor as any).handleInput.bind(this.editor);
    (this.editor as any).handleInput = (data: string) => {
      // Ctrl+C
      if (matchesKey(data, Key.ctrl('c'))) {
        if (this.host.isStreaming()) {
          this.clearPendingExit();
          void this.host.abortStream();
          return;
        }
        if (this.pendingExit?.kind === 'ctrl-c') {
          this.clearPendingExit();
          void this.host.stop(0);
          return;
        }
        this.armPendingExit('ctrl-c', 'Press Ctrl+C again to exit');
        return;
      }

      // Ctrl+D
      if (matchesKey(data, Key.ctrl('d'))) {
        this.clearPendingExit();
        void this.host.stop(0);
        return;
      }

      // Ctrl+O: toggle tool output expansion
      if (matchesKey(data, Key.ctrl('o'))) {
        this.host.onToggleToolExpand?.();
        return;
      }

      // 任何其他输入取消待确认的退出
      if (this.pendingExit) {
        this.clearPendingExit();
        this.host.requestRender();
      }

      originalHandleInput(data);

      // Notify slash command input
      const text = (this.editor as any).getText?.() ?? '';
      this.host.onSlashInput?.(text);
    };
  }

  private armPendingExit(kind: 'ctrl-c', hint: string): void {
    this.clearPendingExit();
    this.footer.setTransientHint(hint);
    this.host.requestRender();

    const timer = setTimeout(() => {
      if (this.pendingExit?.timer === timer) {
        this.clearPendingExit();
        this.host.requestRender();
      }
    }, this.EXIT_CONFIRM_MS);

    this.pendingExit = { kind, timer };
  }

  private clearPendingExit(): void {
    if (!this.pendingExit) return;
    clearTimeout(this.pendingExit.timer);
    this.footer.setTransientHint(null);
    this.pendingExit = null;
  }
}
