/**
 * 授权等待框（OAuth device_code / auth_url 流程的取消入口）。
 *
 * 展示对齐 pi LoginDialogComponent（hyperlink URL + 授权码 + waiting 提示），
 * 取消语义为跨进程对位：组件持有 onCancel 回调（app 注入 cancelRequest
 * 句柄），Esc → 本地关框 + 上行取消——pi 的 AbortController 内聚模式
 * （login-dialog.ts:15）在 RPC 架构下的等价物。
 *
 * 生命周期：app 在 type="auth" 通知到达时创建并替换编辑器槽位；
 * prompt 调用 settle（成功/失败/取消）时关闭。
 */

import chalk from 'chalk';
import {
  Box,
  Container,
  Spacer,
  Text,
  TUI,
  matchesKey,
  type Focusable,
} from '@earendil-works/pi-tui';

export interface AuthWaitingContent {
  url?: string;
  userCode?: string;
  message?: string;
}

export class AuthWaitingDialog extends Container implements Focusable {
  private _focused = false;
  private readonly content = new Container();

  constructor(
    private readonly tui: TUI,
    private readonly onCancel: () => void,
  ) {
    super();
    this.addChild(new Box(1, 0, (s) => chalk.bgCyan.black(s)));
    this.addChild(this.content);
    this.addChild(new Box(1, 0, (s) => chalk.bgCyan.black(s)));
  }

  get focused(): boolean {
    return this._focused;
  }
  set focused(value: boolean) {
    this._focused = value;
  }

  /** device_code / auth_url 载荷展示（可点击 hyperlink + 授权码）。 */
  showAuth(params: AuthWaitingContent): void {
    this.content.clear();
    this.content.addChild(new Spacer(1));
    const url = params.url;
    if (url) {
      const linked = `\x1b]8;;${url}\x07${url}\x1b]8;;\x07`;
      this.content.addChild(new Text(chalk.cyan(linked), 1, 0));
      const hint = process.platform === 'darwin' ? 'Cmd+click to open' : 'Ctrl+click to open';
      this.content.addChild(
        new Text(chalk.dim(`\x1b]8;;${url}\x07${hint}\x1b]8;;\x07`), 1, 0),
      );
    }
    if (params.userCode) {
      this.content.addChild(new Spacer(1));
      this.content.addChild(new Text(chalk.yellow(`Enter code: ${params.userCode}`), 1, 0));
    }
    if (!url && !params.userCode && params.message) {
      this.content.addChild(new Text(chalk.dim(params.message), 1, 0));
    }
    this.showWaiting();
  }

  /** 等待提示（轮询期间常驻）。 */
  showWaiting(): void {
    this.content.addChild(new Spacer(1));
    this.content.addChild(new Text(chalk.dim('Waiting for authentication...'), 1, 0));
    this.content.addChild(new Text(chalk.dim('(Esc to cancel)'), 1, 0));
    this.tui.requestRender();
  }

  handleInput(data: string): void {
    if (matchesKey(data, 'escape')) {
      this.onCancel();
      return;
    }
    // 其余键位吞掉：等待期间编辑器已替换出槽位，输入无处可去
  }
}
