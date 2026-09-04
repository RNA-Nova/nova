/**
 * nova-coding-agent 的扩展入口（frontend/tui/index.ts）。
 *
 * 编程执行能力的 TUI 呈现半区：interactive-shell 终端让位对话框 +
 * bashExecution 条目卡片（user_tools/bash 的消息类型归包呈现——活组件
 * 带 update，流式 chunk/定稿逐次重绘；宿主不内置本卡）。
 *
 * 工具渲染器（tui/tools/<name>.ts）与对话框（tui/dialogs/）走目录发现，
 * 无需在此注册。
 */

import type { ExtensionUIAPI } from 'nova-tui';

import { interactiveShellDialogFactory } from './dialogs/interactive-shell.js';
import { BashExecutionCard } from './lib/bash-execution.js';

export default function extension(api: ExtensionUIAPI): void {
  // interactive-shell 终端让位（dialog:interactive-shell——挂起 TUI
  // 执行交互命令后恢复，回执退出码）
  api.registerDialog?.('interactive-shell', interactiveShellDialogFactory);
  // bashExecution 条目卡片（entry:<customType> 槽）
  api.registerEntryRenderer('bashExecution', (entry) => new BashExecutionCard(entry.data));
}
