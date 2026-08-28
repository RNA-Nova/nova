/**
 * KeymapController：全局键位路由（Esc 域级分派 + ctrl 族）。
 *
 * 键位表驱动（keymap/ 系统）：所有动作经 ``kb.matches(data, actionId)``
 * 匹配——用户 keybindings.json 重绑定立即生效，提示行自动跟随。
 *
 * Esc 三级路由（对齐 pi + cancelRequest 扩展）：
 * - 对话框开着（四件套）→ 让路（组件本地取消，run 继续）；
 * - auth 等待框开着 → 让路（组件内 Esc → onCancel → cancelRequest）；
 * - 无框 → 按会话状态域级分派：working 停整个 run（先还原队列），
 *   retrying/compacting 只停对应域（宽级联 abort 会误伤正在进行的 run）。
 */

import type { NovaUIRuntime } from 'nova-client';
import { shortcutSlot } from 'nova-client';
import {
  getKeybindings,
  isKeyRelease,
  isKeyRepeat,
  matchesKey,
  type KeyId,
} from '@earendil-works/pi-tui';

import { RESERVED_KEYBINDINGS } from '../keymap/tables.js';
import type { DialogController } from './dialogs.js';
import type { EditorController, EditorRef } from './editor.js';
import type { ForegroundTasks } from './foreground.js';
import type { TranscriptController } from './transcript.js';

/** KeymapController 的依赖袋（装配根注入）。 */
export interface KeymapDeps {
  editorRef: EditorRef;
  runtime: NovaUIRuntime;
  dialogs: DialogController;
  transcript: TranscriptController;
  editorController: EditorController;
  /** 前台在飞任务取消登记处（Esc 路由：对话框 > 前台任务 > run/retry/compacting）。 */
  foregroundTasks: ForegroundTasks;
  /** 展开-折叠全局切换（ctrl+o——装配根实现：翻转态 + 各视图联动重建）。 */
  toggleExpansion: () => void;
  /** 双 Esc 导航设置（tree/fork/none——装配根读 settings 后经 getter 现取）。 */
  doubleEscapeAction: () => 'fork' | 'tree' | 'none';
  /** thinking 显隐切换（ctrl+t——装配根实现：settings 取反 + 持久化 + 重建）。 */
  toggleThinking: () => void;
  /** 挂起到后台（ctrl+z——装配根实现：终端让位 + SIGCONT 恢复）。 */
  suspend: () => void;
  /** 外部编辑器编辑草稿（ctrl+g——装配根实现：终端让位 + 退出回写）。 */
  openExternalEditor: () => void;
  quit: (code: number) => void;
}

export class KeymapController {
  private lastCtrlC = 0;
  private lastEscape = 0;
  /** 生效扩展快捷键（keyName → slot 键——对账时重建，含保留键位剔除）。 */
  private readonly activeShortcuts = new Map<string, string>();

  constructor(private readonly deps: KeymapDeps) {}

  /**
   * scoped 池循环 + 结果反馈（ctrl+p 族）。
   * 后端 success:false（池内已配置模型不足两个）此前被静默吞掉——"按了没反应"
   * 的体感根因；成功路径的 footer 更新靠 model_changed 事件（Bus 2）。
   */
  private async cycleModelWithFeedback(direction: 'forward' | 'backward'): Promise<void> {
    const { runtime } = this.deps;
    try {
      const result = await runtime.cycleModel(direction);
      if (result && result.success === false) {
        runtime.store.addNotice(
          'info',
          '无法循环：scoped 池中已配置凭据的模型不足两个（/scoped-models 调整）',
        );
        return;
      }
      if (result?.model) {
        const thinking = result.thinkingLevel ? ` · ${result.thinkingLevel}` : '';
        runtime.store.addNotice(
          'info',
          `已切换模型: ${result.model.provider}/${result.model.id}${thinking}`,
        );
      }
    } catch {
      runtime.store.addNotice('error', '模型循环失败');
    }
  }

  /**
   * 扩展快捷键对账（restrictOverride 消费——slots 整体替换后由装配根调用）：
   * 撞保留键位当前绑定键的扩展快捷键禁用 + 诊断（pi RESERVED 语义对位）。
   */
  validateExtensionShortcuts(): void {
    this.activeShortcuts.clear();
    const kb = getKeybindings();
    const reservedKeys = new Set(
      RESERVED_KEYBINDINGS.flatMap((id) => kb.getKeys(id as never) as string[]),
    );
    for (const { key, source } of this.deps.runtime.slots.list()) {
      if (!key.startsWith('shortcut:')) continue;
      const keyName = key.slice('shortcut:'.length);
      if (reservedKeys.has(keyName)) {
        this.deps.transcript.addInfo(
          `扩展快捷键 ${keyName}（来源 ${source}）撞保留键位，已禁用`,
        );
        continue;
      }
      this.activeShortcuts.set(keyName, key);
    }
  }

  /** 对话框/授权框开着时让路（组件本地取消优先于域级路由）。 */
  private get dialogsActive(): boolean {
    return this.deps.dialogs.isActive || this.deps.dialogs.hasAuthDialog;
  }

  /** 全局键位入口（tui.addInputListener）。返回 {consume:true} 拦截，否则让路。 */
  handle(data: string): { consume: true } | undefined {
    // kitty 键盘协议 flag 2 下终端会为一次物理按键发 press+repeat+release
    // 三个事件（VS Code 终端即如此）。pi-tui 对焦点组件过滤 release，但
    // inputListeners（本控制器）不过滤——release 同样命中 matchesKey，
    // 导致"单击 ctrl+c 被当成 500ms 内双击"直接退出。全局动作一律只认
    // press：release/repeat 直接让路（编辑器文本重复输入不受影响——
    // 那是焦点组件路径，不经这里）。
    if (isKeyRelease(data) || isKeyRepeat(data)) return undefined;

    const kb = getKeybindings();
    const { editorRef, runtime, transcript, editorController } = this.deps;

    // 扩展快捷键最优先（pi onExtensionShortcut 对位——先于一切内建路由）；
    // 生效表经对账重建（撞保留键位的已剔除）
    if (!this.dialogsActive) {
      for (const [keyName, slotKey] of this.activeShortcuts) {
        if (matchesKey(data, keyName as KeyId)) {
          const handler = runtime.slots.resolve<never, unknown>(slotKey);
          if (handler !== undefined) {
            void Promise.resolve((handler as () => unknown)()).catch(() => undefined);
            return { consume: true };
          }
        }
      }
    }

    // ctrl+c：清空编辑器；500ms 内双击退出（防误退）
    if (kb.matches(data, 'app.clear')) {
      const now = Date.now();
      if (now - this.lastCtrlC < 500) this.deps.quit(0);
      this.lastCtrlC = now;
      editorRef.current.setText('');
      return { consume: true };
    }
    // ctrl+d：编辑器空时退出
    if (kb.matches(data, 'app.exit') && editorRef.current.getText() === '') {
      this.deps.quit(0);
      return { consume: true };
    }
    // Esc：域级分派
    if (kb.matches(data, 'app.interrupt')) {
      if (this.dialogsActive) return undefined;
      // 前台在飞任务（分支摘要/gist 创建等非 run 任务）优先于 run 域
      if (this.deps.foregroundTasks.consume()) return { consume: true };
      const status = runtime.store.status;
      if (status === 'working') {
        // pi 语义：中断前先把排队消息还原进编辑器（队列内容不丢）
        void editorController
          .dequeueToEditor()
          .finally(() => runtime.abort().catch(() => undefined));
        return { consume: true };
      }
      if (status === 'retrying') {
        void runtime.abortRetry().catch(() => undefined);
        return { consume: true };
      }
      if (status === 'compacting') {
        void runtime.abortCompaction().catch(() => undefined);
        return { consume: true };
      }
      // idle + 空编辑器：双击 Esc 导航（pi 同款 500ms 窗，设置档 tree/fork/none）
      if (editorRef.current.getText().trim() === '') {
        const action = this.deps.doubleEscapeAction();
        if (action !== 'none') {
          const now = Date.now();
          if (now - this.lastEscape < 500) {
            this.lastEscape = 0;
            editorController.openNavigation(action);
            return { consume: true };
          }
          this.lastEscape = now;
        }
      }
    }
    // ctrl+o：展开-折叠全局切换（装配根回调：transcript/welcome/resources 联动）
    if (kb.matches(data, 'app.tools.expand')) {
      // 对话框开着时让路（组件本地键位优先——tree 选择器的 filter 循环等）
      if (this.dialogsActive) return undefined;
      this.deps.toggleExpansion();
      return { consume: true };
    }
    // ctrl+v：剪贴板粘贴（图片 → 临时文件路径；否则文本）
    if (kb.matches(data, 'app.clipboard.paste')) {
      if (this.dialogsActive) return undefined;
      void editorController.pasteFromClipboard();
      return { consume: true };
    }
    // ctrl+x：复制最后一条 assistant 消息
    if (kb.matches(data, 'app.message.copy')) {
      if (this.dialogsActive) return undefined;
      void transcript.copyLastAssistantMessage();
      return { consume: true };
    }
    // ctrl+z：挂起到后台（fg 恢复——装配根实现终端让位/恢复）
    if (kb.matches(data, 'app.suspend')) {
      if (this.dialogsActive) return undefined;
      this.deps.suspend();
      return { consume: true };
    }
    // ctrl+g：外部编辑器编辑草稿（退出回写——装配根实现）
    if (kb.matches(data, 'app.editor.external')) {
      if (this.dialogsActive) return undefined;
      this.deps.openExternalEditor();
      return { consume: true };
    }
    // alt+enter：follow-up 排队（working 时等 run 结束发送；idle 等同普通提交）
    if (kb.matches(data, 'app.message.followUp')) {
      if (this.dialogsActive) return undefined;
      const text = editorRef.current.getText().trim();
      if (text) {
        editorRef.current.addToHistory?.(text);
        editorRef.current.setText('');
        editorController.submitText(text, { followUp: true });
      }
      return { consume: true };
    }
    // alt+↑：队列还原进编辑器（steering + follow-up 全量）
    if (kb.matches(data, 'app.message.dequeue')) {
      if (this.dialogsActive) return undefined;
      void editorController.dequeueToEditor();
      return { consume: true };
    }
    // shift+tab：循环 thinking 级别（后端按模型支持面循环）
    if (kb.matches(data, 'app.thinking.cycle')) {
      if (this.dialogsActive) return undefined;
      void runtime.cycleThinkingLevel().catch(() => undefined);
      return { consume: true };
    }
    // ctrl+t：thinking 块显隐（持久化 + transcript 重建——装配根回调）
    if (kb.matches(data, 'app.thinking.toggle')) {
      if (this.dialogsActive) return undefined;
      this.deps.toggleThinking();
      return { consume: true };
    }
    // ctrl+p / shift+ctrl+p：scoped 池模型循环（后端 cycleModel）
    if (kb.matches(data, 'app.model.cycleForward')) {
      if (this.dialogsActive) return undefined;
      void this.cycleModelWithFeedback('forward');
      return { consume: true };
    }
    if (kb.matches(data, 'app.model.cycleBackward')) {
      if (this.dialogsActive) return undefined;
      void this.cycleModelWithFeedback('backward');
      return { consume: true };
    }
    // ctrl+l：模型选择器（推 '/model' 命令——bundle 包自持选择器，缺席走后端回退）
    if (kb.matches(data, 'app.model.select')) {
      if (this.dialogsActive) return undefined;
      editorController.runCommand('model');
      return { consume: true };
    }
    return undefined;
  }
}
