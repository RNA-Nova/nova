/**
 * TerminalController 的 OSC 发射集中点（pi tui/terminal.ts 与
 * interactive-mode 的终端集成层对位）。
 *
 * - ``updateTitle``：OSC 0 窗口标题（``nova - <会话名> - <cwd基名>``，
 *   无会话名省掉 name 段——pi updateTerminalTitle 对位）；
 * - ``updateProgress``：OSC 9;4 终端进度（working/compacting 置
 *   indeterminate，其余清除；1s keepalive——pi setProgress 同款，
 *   tmux 等会丢弃一次性进度序列）——受 ``terminal_progress`` 设置门控；
 * - ``notifyTurnEnded``：agent run 结束的桌面通知（OSC 9 iTerm2/WezTerm +
 *   OSC 777 rxvt 系 + OSC 99 kitty 三序列并发，互不认识的终端各自忽略——
 *   pi notify.ts 对位；一次性序列，退出前无需清理）——受 ``desktop_notify``
 *   设置门控；
 * - ``initTerminalIntegration`` / ``applyFrontendSettings``：编辑器内边距 /
 *   补全可见条数 / clearOnShrink 的运行时应用（pi-tui Editor/TUI 均有
 *   setter——设置面板变更即时生效，无需重启）。
 *
 * 装配（归 app.ts）：启动 ``initTerminalIntegration({ tui, editorRef })``；
 * store 订阅里 snapshot 变化 → ``updateTitle(snapshot)``、status 变化 →
 * ``updateProgress(status)``；退出前 ``clearTerminalProgress()``。
 */

import type { SessionSnapshot, SessionStatus } from 'nova-tui';
import { basename } from 'node:path';

import type { TUI } from '@earendil-works/pi-tui';

import type { EditorRef } from './editor.js';
import {
  getAutocompleteMaxItems,
  getEditorPadding,
  isClearOnShrink,
  isDesktopNotifyEnabled,
  isTerminalProgressEnabled,
} from '../utils/tui-settings.js';

// ---------------------------------------------------------------------------
// 写入出口（测试可注入假 writer 捕获序列）
// ---------------------------------------------------------------------------

let writer: (data: string) => void = (data) => process.stdout.write(data);

/** 测试注入：替换 stdout 写入（配合 resetTerminalForTest 还原）。 */
export function setTerminalWriterForTest(write?: (data: string) => void): void {
  writer = write ?? ((data) => process.stdout.write(data));
}

// ---------------------------------------------------------------------------
// OSC 0 窗口标题
// ---------------------------------------------------------------------------

let lastTitle: string | undefined;
/** 扩展标题覆盖（setTitle 原语——设置后宿主自动标题停写，清除后恢复）。 */
let titleOverride: string | undefined;

/** 控制字符净化（ESC/BEL/换行会截断甚至注入序列——会话名是后端数据，不设防）。 */
function sanitizeOscPart(value: string): string {
  // oxlint-disable-next-line no-control-regex
  return value.replace(/[\x00-\x1f\x7f]/g, '').trim();
}

function writeTitle(title: string): void {
  if (title === lastTitle) return;
  lastTitle = title;
  writer(`\x1b]0;${title}\x07`);
}

/** 扩展设置/清除终端标题覆盖（undefined 恢复自动标题——需下一次 updateTitle 生效）。 */
export function setTitleOverride(text: string | undefined): void {
  titleOverride = text === undefined ? undefined : sanitizeOscPart(text);
  if (titleOverride !== undefined) {
    lastTitle = undefined; // 强制重写（覆盖可能与当前自动标题同文）
    writeTitle(titleOverride);
  } else {
    lastTitle = undefined; // 让下一次 updateTitle 无条件重写回自动标题
  }
}

/** 更新终端窗口标题（去重——快照订阅每次变化都调，内容没变不重写）。 */
export function updateTitle(snapshot: SessionSnapshot | null | undefined): void {
  if (titleOverride !== undefined) return; // 扩展覆盖在位：自动标题停写
  const name = sanitizeOscPart(snapshot?.sessionName ?? '');
  const cwd = snapshot?.cwd ?? process.cwd();
  const dir = sanitizeOscPart(basename(cwd)) || cwd;
  const title = `nova - ${name ? `${name} - ` : ''}${dir}`;
  writeTitle(title);
}

// ---------------------------------------------------------------------------
// OSC 9;4 终端进度（indeterminate；清除 = 9;4;0）
// ---------------------------------------------------------------------------

const PROGRESS_ACTIVE_SEQUENCE = '\x1b]9;4;3\x07';
const PROGRESS_CLEAR_SEQUENCE = '\x1b]9;4;0;\x07';
const PROGRESS_KEEPALIVE_MS = 1000;

let progressActive = false;
let progressKeepalive: ReturnType<typeof setInterval> | undefined;

function stopProgressKeepalive(): void {
  if (progressKeepalive !== undefined) {
    clearInterval(progressKeepalive);
    progressKeepalive = undefined;
  }
}

/** 清除终端进度（退出前/设置关闭时调用——幂等）。 */
export function clearTerminalProgress(): void {
  if (!progressActive) return;
  progressActive = false;
  stopProgressKeepalive();
  writer(PROGRESS_CLEAR_SEQUENCE);
}

/** 会话状态 → 终端进度（working/compacting 置位，其余清除；设置关闭时只清不置）。 */
export function updateProgress(status: SessionStatus): void {
  const busy = status === 'working' || status === 'compacting';
  if (!busy || !isTerminalProgressEnabled()) {
    clearTerminalProgress();
    return;
  }
  if (progressActive) return; // 已置位（keepalive 在跑）
  progressActive = true;
  writer(PROGRESS_ACTIVE_SEQUENCE);
  // keepalive：部分终端（tmux 等）会丢弃一次性进度序列——pi 同款 1s 重发
  progressKeepalive = setInterval(() => writer(PROGRESS_ACTIVE_SEQUENCE), PROGRESS_KEEPALIVE_MS);
  progressKeepalive.unref();
}

// ---------------------------------------------------------------------------
// 桌面通知（OSC 9 iTerm2/WezTerm + OSC 777 rxvt 系 + OSC 99 kitty）
// ---------------------------------------------------------------------------

/**
 * agent run 结束的桌面通知：三条序列并发写出（终端只认自己支持的那条，
 * 其余忽略）。一次性序列——无 keepalive，退出前无需清理。
 */
export function notifyTurnEnded(title: string, body: string): void {
  if (!isDesktopNotifyEnabled()) return;
  const safeTitle = sanitizeOscPart(title);
  const safeBody = sanitizeOscPart(body);
  writer(`\x1b]9;${safeBody}\x07`);
  writer(`\x1b]777;notify;${safeTitle};${safeBody}\x07`);
  writer(`\x1b]99;i=1:d=0;${safeTitle}\x1b\\`);
}

// ---------------------------------------------------------------------------
// 前端设置即时应用（编辑器/清屏行为——pi-tui setter 运行时生效）
// ---------------------------------------------------------------------------

let boundTui: TUI | undefined;
let boundEditorRef: EditorRef | undefined;

export interface TerminalIntegrationDeps {
  tui?: TUI;
  editorRef?: EditorRef;
}

/** 装配入口：绑定 TUI/编辑器引用 + 按持久化设置应用一次。 */
export function initTerminalIntegration(deps: TerminalIntegrationDeps): void {
  boundTui = deps.tui;
  boundEditorRef = deps.editorRef;
  applyFrontendSettings();
}

/** 重新读取前端设置并应用（设置面板 onChange 调用；未绑定时安全 no-op）。 */
export function applyFrontendSettings(): void {
  boundTui?.setClearOnShrink(isClearOnShrink());
  const editor = boundEditorRef?.current;
  editor?.setPaddingX?.(getEditorPadding());
  editor?.setAutocompleteMaxVisible?.(getAutocompleteMaxItems());
}

/** 测试还原：清 keepalive/绑定/标题缓存（防用例间泄漏）。 */
export function resetTerminalForTest(): void {
  stopProgressKeepalive();
  progressActive = false;
  lastTitle = undefined;
  titleOverride = undefined;
  boundTui = undefined;
  boundEditorRef = undefined;
  setTerminalWriterForTest(undefined);
}
