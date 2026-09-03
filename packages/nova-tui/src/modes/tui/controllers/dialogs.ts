/**
 * 对话框控制器与四件套组件（pi-tui 现成件组装）。
 *
 * 模式对齐 pi interactive-mode：对话框**替换编辑器槽位**（转录区不动），
 * 应答/取消后恢复编辑器。路由：
 * - onUIRequest → select/confirm/input 对话框 → sendUIResponse
 * - onUICancel  → 后端 abort 撤销 → 关框并按 cancelled 应答
 * - onUINotice  → status 槽位显示通知（不进转录）
 */

import chalk from 'chalk';
import type { NovaUIRuntime, UINotice, UIRequest } from 'nova-tui';
import type { NovaOverlayOptions } from 'nova-tui';
import {
  Box,
  Container,
  Editor,
  Input,
  SelectList,
  Text,
  TUI,
  matchesKey,
  type Component,
  type Focusable,
  type OverlayHandle,
  type OverlayOptions,
  type SelectListTheme,
} from '@earendil-works/pi-tui';

import type { EditorRef } from './editor.js';
import type { StatusController } from './status.js';
import { AuthWaitingDialog } from '../components/dialogs/auth-waiting.js';
import { FormDialog, type FormFieldSpec } from '../components/dialogs/form.js';
import { SearchableSelector, type SearchableItem } from '../components/pickers/searchable.js';
import type { RegionEnv } from '../components/layout/region-host.js';
import { editorTheme } from '../themes/index.js';

const selectListTheme: SelectListTheme = {
  selectedPrefix: (s) => chalk.cyan(s),
  selectedText: (s) => chalk.cyan(s),
  description: (s) => chalk.gray(s),
  scrollInfo: (s) => chalk.dim(s),
  noMatch: (s) => chalk.yellow(s),
};

/** 多行编辑对话框（editor 原语组件）：enter 提交、esc 取消。 */
class EditorDialog extends Container implements Focusable {
  private _focused = false;
  private readonly editor: Editor;

  constructor(
    tui: TUI,
    title: string,
    prefill: string,
    private readonly onDone: (value: string) => void,
    private readonly onCancel: () => void,
  ) {
    super();
    this.editor = new Editor(tui, editorTheme, { paddingX: 1 });
    if (prefill) this.editor.setText(prefill);
    this.editor.onSubmit = (text) => this.onDone(text);
    this.addChild(new Box(1, 0, (s) => chalk.bgCyan.black(s)));
    this.addChild(new Text(` ${title} `, 0, 0));
    this.addChild(this.editor);
    this.addChild(new Text(chalk.dim(' enter 提交 · shift+enter 换行 · esc 取消'), 1, 0));
  }

  get focused(): boolean {
    return this._focused;
  }
  set focused(value: boolean) {
    this._focused = value;
    this.editor.focused = value;
  }

  handleInput(data: string): void {
    if (matchesKey(data, 'escape')) {
      this.onCancel();
      return;
    }
    this.editor.handleInput(data);
  }
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

/** select 参数 → 选择器条目：结构化 items 优先（元信息列），options 字符串数组兜底。 */
function itemsFromParams(params: Record<string, unknown>): SearchableItem[] {
  const items = params.items;
  if (Array.isArray(items) && items.length > 0) {
    return items
      .filter((item) => typeof item === 'object' && item !== null)
      .map((item) => {
        const record = item as Record<string, unknown>;
        const value = typeof record.value === 'string' ? record.value : '';
        const label = typeof record.label === 'string' ? record.label : value;
        const description =
          typeof record.description === 'string' ? record.description : undefined;
        // 定制视觉元信息（树形缩进/分组头——后端 select_items 可选供给）
        const depth =
          typeof record.depth === 'number' && record.depth >= 0 ? record.depth : undefined;
        const group = typeof record.group === 'string' ? record.group : undefined;
        return { value, label, description, depth, group };
      })
      .filter((item) => item.value);
  }
  return strList(params.options).map((option) => ({ value: option, label: option }));
}

export class DialogController {
  private activeId: string | undefined;
  /** 授权等待框（type="auth" 通知驱动；最多一个，prompt settle 时关闭）。 */
  private authDialog: AuthWaitingDialog | undefined;
  /** 在飞 slash 命令调用的取消句柄（cancelRequest；settle 即清）。 */
  private pendingCommandCancel: (() => void) | undefined;
  /** 前端本地对话框（/theme 选择器等——无 RPC id，本地开关）。 */
  private localDialog = false;

  constructor(
    private readonly tui: TUI,
    private readonly editorContainer: Container,
    private readonly editorRef: EditorRef,
    private readonly status: StatusController,
    private readonly runtime: NovaUIRuntime,
    /** custom 原语的工厂环境（RegionEnv：cwd/tui/colors/markdownTheme——装配根注入）。 */
    private readonly customEnv: RegionEnv,
    /** set_status 命名通知的出口（footer 扩展状态行——装配根注入，pi setStatus 对位）。 */
    private readonly onExtensionStatus?: (key: string, text: string | undefined) => void,
  ) {
    this.runtime.onUIRequest((req) => this.handle(req));
    this.runtime.onUICancel((id) => this.dismiss(id));
    this.runtime.onUINotice((notice) => this.showNotice(notice));
  }

  /** 是否有活动对话框（Esc 两级路由：有框 Esc 归框，无框 Esc 才 abort run）。 */
  get isActive(): boolean {
    return this.activeId !== undefined || this.localDialog;
  }

  /** 授权等待框是否开着（Esc 让路判断）。 */
  get hasAuthDialog(): boolean {
    return this.authDialog !== undefined;
  }

  /** 前端本地对话框是否开着（/theme、/settings 等——本地框间切换的重入判定用）。 */
  get hasLocalDialog(): boolean {
    return this.localDialog;
  }

  /** 注册在飞命令的取消句柄（editor 提交 slash 命令时调用）。 */
  setPendingCommandCancel(cancel: () => void): void {
    this.pendingCommandCancel = cancel;
  }

  /** 命令 settle：清句柄 + 关授权等待框（幂等）。 */
  clearPendingCommand(): void {
    this.pendingCommandCancel = undefined;
    this.closeAuthDialog();
  }

  /** 授权等待通知（type="auth"）：开等待框并注入取消句柄。 */
  private showAuthDialog(params: Record<string, unknown>): void {
    if (this.authDialog) return; // 已在等待框中：重复通知不叠加
    const dialog = new AuthWaitingDialog(this.tui, () => {
      const cancel = this.pendingCommandCancel;
      this.closeAuthDialog();
      cancel?.(); // 先关框再取消（cancel 的 reject → finally 再关一次，幂等）
    });
    this.authDialog = dialog;
    dialog.showAuth({
      url: str(params.url),
      userCode: str(params.userCode),
      message: str(params.message),
    });
    this.editorContainer.clear();
    this.editorContainer.addChild(dialog);
    this.tui.setFocus(dialog);
    this.tui.requestRender();
  }

  /** 关闭授权等待框并恢复编辑器槽位（幂等）。 */
  private closeAuthDialog(): void {
    if (!this.authDialog) return;
    this.authDialog = undefined;
    this.editorContainer.clear();
    this.editorContainer.addChild(this.editorRef.current);
    this.tui.setFocus(this.editorRef.current);
    this.tui.requestRender();
  }

  private handle(req: UIRequest): void {
    switch (req.component) {
      case 'select':
        this.showSelect(req);
        return;
      case 'confirm':
        this.showConfirm(req);
        return;
      case 'input':
        this.showInput(req);
        return;
      case 'form':
        this.showForm(req);
        return;
      default:
        // dialog:<name>：包侧自定义对话框（slot 键即 componentType——工厂产
        // 组件挂模态，done(result) 应答；未注册按 cancelled（不挂起后端）
        if (req.component.startsWith('dialog:')) {
          this.showCustomDialog(req);
          return;
        }
        // 未知原语：按 cancelled 应答（不挂起后端）
        this.runtime.sendUIResponse(req.id, { cancelled: true });
    }
  }

  /**
   * 包侧自定义对话框（dialog:* slot——pi 后端扩展直驱 UI 的对位）：
   * 工厂形态 ``(env, params, done) => Component``；done(undefined) = 取消
   * （cancelled 应答），其余值按 ``{value: result}`` 应答。撤销帧
   * （ui/cancel）经 activeId 复用 dismiss 路径（与基线对话框同语义）。
   */
  private showCustomDialog(req: UIRequest): void {
    const factory = this.runtime.slots.resolve(req.component) as
      | ((env: unknown, params: Record<string, unknown>, done: (result?: unknown) => void) => unknown)
      | undefined;
    if (typeof factory !== 'function') {
      this.runtime.sendUIResponse(req.id, { cancelled: true });
      return;
    }
    let component: unknown;
    try {
      component = factory(this.customEnv, req.params, (result?: unknown) => {
        this.restore();
        if (result === undefined) {
          this.runtime.sendUIResponse(req.id, { cancelled: true });
        } else {
          this.runtime.sendUIResponse(req.id, { value: result });
        }
      });
    } catch {
      this.runtime.sendUIResponse(req.id, { cancelled: true });
      return;
    }
    if (
      typeof component !== 'object' ||
      component === null ||
      typeof (component as { render?: unknown }).render !== 'function'
    ) {
      this.runtime.sendUIResponse(req.id, { cancelled: true });
      return;
    }
    this.swap(req.id, component as Container, component as Component);
  }

  private showSelect(req: UIRequest): void {
    const title = str(req.params.title);
    // 结构化 items 优先（元信息列）；options（字符串数组）为兼容快捷形式
    const items = itemsFromParams(req.params);
    const selector = new SearchableSelector(
      title,
      items,
      {
        onSelect: (value) => {
          this.restore();
          this.runtime.sendUIResponse(req.id, { value });
        },
        onCancel: () => {
          this.restore();
          this.runtime.sendUIResponse(req.id, { cancelled: true });
        },
      },
      { placeholder: str(req.params.placeholder) || undefined },
    );
    this.swap(req.id, selector, selector);
  }

  private showConfirm(req: UIRequest): void {
    const title = str(req.params.title);
    const message = str(req.params.message);
    const items = [
      { value: 'yes', label: 'Yes' },
      { value: 'no', label: 'No' },
    ];
    const list = new SelectList(items, 2, selectListTheme);
    list.onSelect = (item) => {
      this.restore();
      this.runtime.sendUIResponse(req.id, { confirmed: item.value === 'yes' });
    };
    list.onCancel = () => {
      this.restore();
      this.runtime.sendUIResponse(req.id, { cancelled: true });
    };

    const dialog = new Container();
    dialog.addChild(new Box(1, 0, (s) => chalk.bgCyan.black(s)));
    dialog.addChild(new Text(` ${title}${message ? ` — ${message}` : ''} `, 0, 0));
    dialog.addChild(list);
    this.swap(req.id, dialog, list);
  }

  private showInput(req: UIRequest): void {
    const title = str(req.params.title);
    const placeholder = str(req.params.placeholder);
    const input = new Input();
    if (placeholder) input.setValue(placeholder);
    input.onSubmit = (value) => {
      this.restore();
      this.runtime.sendUIResponse(req.id, { value });
    };
    input.onEscape = () => {
      this.restore();
      this.runtime.sendUIResponse(req.id, { cancelled: true });
    };

    const dialog = new Container();
    dialog.addChild(new Box(1, 0, (s) => chalk.bgCyan.black(s)));
    dialog.addChild(new Text(` ${title} `, 0, 0));
    dialog.addChild(input);
    this.swap(req.id, dialog, input);
  }

  /** form 原语：多字段表单（fields=[{key,label,placeholder?}]），应答 {value: {key: text}}。 */
  private showForm(req: UIRequest): void {
    const title = str(req.params.title);
    const rawFields = Array.isArray(req.params.fields) ? req.params.fields : [];
    const fields: FormFieldSpec[] = rawFields
      .filter((f) => typeof f === 'object' && f !== null)
      .map((f) => {
        const record = f as Record<string, unknown>;
        return {
          key: str(record.key),
          label: str(record.label) || str(record.key),
          placeholder: str(record.placeholder) || undefined,
        };
      })
      .filter((f) => f.key);
    if (fields.length === 0) {
      // 无有效字段：按 cancelled 应答（不挂起后端）
      this.runtime.sendUIResponse(req.id, { cancelled: true });
      return;
    }
    const dialog = new FormDialog(title, fields, {
      onSubmit: (values) => {
        this.restore();
        this.runtime.sendUIResponse(req.id, { value: values });
      },
      onCancel: () => {
        this.restore();
        this.runtime.sendUIResponse(req.id, { cancelled: true });
      },
    });
    this.swap(req.id, dialog, dialog);
  }

  /** 后端撤销（abort 竞速胜出）：关闭当前对话框并按 cancelled 应答。 */
  private dismiss(id: string): void {
    if (this.activeId !== id) return;
    this.restore();
    this.runtime.sendUIResponse(id, { cancelled: true });
  }

  private showNotice(notice: UINotice): void {
    // set_status 命名通知（pi ctx.ui.setStatus 对位——后端驱动的 footer 扩展
    // 状态行：key 幂等覆盖，空文本清除）；未注入出口时静默降级
    if (notice.name === 'set_status') {
      const key = str(notice.params.key);
      if (!key) return;
      const text = str(notice.params.text);
      this.onExtensionStatus?.(key, text || undefined);
      return;
    }
    const type = str(notice.params.type, 'info');
    if (type === 'auth') {
      // 授权等待通知：开等待框（结构化载荷 url/userCode），不进 status 槽位
      this.showAuthDialog(notice.params);
      return;
    }
    const message = str(notice.params.message);
    if (!message) {
      // 空 progress 通知 = 清除状态槽（登录流程结束清掉 Waiting 提示等）
      if (type === 'progress') this.status.clearNotice();
      return;
    }
    this.status.showNotice(message, type);
  }

  private swap(id: string, dialog: Container, focusTarget: Component): void {
    this.activeId = id;
    this.editorContainer.clear();
    this.editorContainer.addChild(dialog);
    this.tui.setFocus(focusTarget);
    this.tui.requestRender();
  }

  private restore(): void {
    this.activeId = undefined;
    this.editorContainer.clear();
    this.editorContainer.addChild(this.editorRef.current);
    this.tui.setFocus(this.editorRef.current);
    this.tui.requestRender();
  }

  /** 打开前端本地对话框（无 RPC id——/theme 选择器等本地交互）。 */
  showLocal(dialog: Container, focusTarget: Component): void {
    this.localDialog = true;
    this.editorContainer.clear();
    this.editorContainer.addChild(dialog);
    this.tui.setFocus(focusTarget);
    this.tui.requestRender();
  }

  /** 关闭本地对话框并恢复编辑器槽位（幂等）。 */
  restoreLocal(): void {
    if (!this.localDialog) return;
    this.localDialog = false;
    this.editorContainer.clear();
    this.editorContainer.addChild(this.editorRef.current);
    this.tui.setFocus(this.editorRef.current);
    this.tui.requestRender();
  }

  // ---------------------------------------------------------------------------
  // 扩展 UI 上下文的本地原语实现（Promise 化——ExtensionUIContext 的 TUI 宿主侧）
  // ---------------------------------------------------------------------------

  /** 本地选择器（select 原语）。 */
  selectLocal(
    title: string,
    items: Array<{ value: string; label: string; description?: string }>,
  ): Promise<string | undefined> {
    return new Promise((resolve) => {
      const selector = new SearchableSelector(
        title,
        items,
        {
          onSelect: (value) => {
            this.restoreLocal();
            resolve(value);
          },
          onCancel: () => {
            this.restoreLocal();
            resolve(undefined);
          },
        },
        {},
      );
      this.showLocal(selector, selector);
    });
  }

  /** 本地确认框（confirm 原语）。 */
  confirmLocal(title: string, message: string): Promise<boolean> {
    return new Promise((resolve) => {
      const items = [
        { value: 'yes', label: 'Yes' },
        { value: 'no', label: 'No' },
      ];
      const list = new SelectList(items, 2, selectListTheme);
      list.onSelect = (item) => {
        this.restoreLocal();
        resolve(item.value === 'yes');
      };
      list.onCancel = () => {
        this.restoreLocal();
        resolve(false);
      };
      const dialog = new Container();
      dialog.addChild(new Box(1, 0, (s) => chalk.bgCyan.black(s)));
      dialog.addChild(new Text(` ${title}${message ? ` — ${message}` : ''} `, 0, 0));
      dialog.addChild(list);
      this.showLocal(dialog, list);
    });
  }

  /** 本地单行输入框（input 原语）。 */
  inputLocal(title: string, placeholder?: string): Promise<string | undefined> {
    return new Promise((resolve) => {
      const input = new Input();
      if (placeholder) input.setValue(placeholder);
      input.onSubmit = (value) => {
        this.restoreLocal();
        resolve(value);
      };
      input.onEscape = () => {
        this.restoreLocal();
        resolve(undefined);
      };
      const dialog = new Container();
      dialog.addChild(new Box(1, 0, (s) => chalk.bgCyan.black(s)));
      dialog.addChild(new Text(` ${title} `, 0, 0));
      dialog.addChild(input);
      this.showLocal(dialog, input);
    });
  }

  /** 本地多行编辑器框（editor 原语——pi extension-editor 对位：enter 提交、esc 取消）。 */
  editorLocal(title: string, prefill?: string): Promise<string | undefined> {
    return new Promise((resolve) => {
      const dialog = new EditorDialog(
        this.tui,
        title,
        prefill ?? '',
        (value) => {
          this.restoreLocal();
          resolve(value);
        },
        () => {
          this.restoreLocal();
          resolve(undefined);
        },
      );
      this.showLocal(dialog, dialog);
    });
  }

  /**
   * 模态自定义对话框（custom 原语——逃生舱核心件，pi ctx.ui.custom 对位）。
   * 工厂产出组件挂载进编辑器槽位（或 overlay 浮层）；组件经 done(result)
   * 交还结果并关框；Esc 等键位语义归组件自管（作者职责，pi 同款）。
   * 工厂抛错/产物非组件 → 按 undefined 解决（不挂起扩展）。
   */
  customLocal<T>(
    factory: (env: RegionEnv, done: (result?: T) => void) => unknown,
    options?: { overlay?: NovaOverlayOptions },
  ): Promise<T | undefined> {
    return new Promise((resolve) => {
      let settled = false;
      let overlayHandle: OverlayHandle | undefined;
      const done = (result?: T) => {
        if (settled) return; // 幂等（组件多次 done 不重复解决）
        settled = true;
        if (overlayHandle) {
          try {
            overlayHandle.hide();
          } catch {
            // hide 异常静默
          }
        } else {
          this.restoreLocal();
        }
        resolve(result);
      };
      let component: unknown;
      try {
        component = factory(this.customEnv, done);
      } catch {
        resolve(undefined); // 建厂即抛错——按取消解决
        return;
      }
      if (
        typeof component !== 'object' ||
        component === null ||
        typeof (component as { render?: unknown }).render !== 'function'
      ) {
        resolve(undefined); // 非组件产物——按取消解决
        return;
      }
      if (options?.overlay) {
        overlayHandle = this.tui.showOverlay(
          component as Component,
          options.overlay as OverlayOptions,
        );
        this.localDialog = true; // Esc 域级路由让路（浮层自管键位）
      } else {
        this.showLocal(component as Container, component as Component);
      }
    });
  }
}
