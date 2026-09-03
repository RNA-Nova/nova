/**
 * Nova 键位定义表（pi keybindings.ts 对位）。
 *
 * 两层：
 * - ``TUI_KEYBINDINGS``（pi-tui 内建）：编辑器移动/删除/undo/kill-ring、
 *   输入提交、选择器导航——pi-tui 组件内部经全局键位表自取；
 * - ``APP_KEYBINDINGS``（本文件）：app 级全局动作（中断/清屏/退出/展开/
 *   剪贴板粘贴）——KeymapController 经 ``kb.matches(data, actionId)`` 消费。
 *
 * 用户键位（keybindings.json）按 actionId 整体替换默认键——改表即生效，
 * 组件与提示行（pickers/hints.ts）自动跟随。
 */

import {
  TUI_KEYBINDINGS,
  type KeybindingDefinitions,
} from '@earendil-works/pi-tui';

/** app 级键位（declaration merging 进 pi-tui 的 Keybindings 注册表）。 */
export interface AppKeybindings {
  'app.interrupt': true;
  'app.clear': true;
  'app.exit': true;
  'app.tools.expand': true;
  'app.clipboard.paste': true;
  'app.message.followUp': true;
  'app.message.dequeue': true;
  'app.thinking.cycle': true;
  'app.thinking.toggle': true;
  'app.model.cycleForward': true;
  'app.model.cycleBackward': true;
  'app.model.select': true;
  'app.message.copy': true;
  'app.suspend': true;
  'app.editor.external': true;
}

export type AppKeybinding = keyof AppKeybindings;

declare module '@earendil-works/pi-tui' {
  interface Keybindings extends AppKeybindings {}
}

export const APP_KEYBINDINGS = {
  'app.interrupt': { defaultKeys: 'escape', description: 'Cancel or abort' },
  'app.clear': {
    defaultKeys: 'ctrl+c',
    description: 'Clear editor (double-press to quit)',
  },
  'app.exit': { defaultKeys: 'ctrl+d', description: 'Exit when editor is empty' },
  'app.tools.expand': { defaultKeys: 'ctrl+o', description: 'Toggle tool output expansion' },
  'app.clipboard.paste': {
    defaultKeys: 'ctrl+v',
    description: 'Paste from clipboard (image → temp file path)',
  },
  'app.message.followUp': {
    defaultKeys: 'alt+enter',
    description: 'Queue follow-up message (send after current turn)',
  },
  'app.message.dequeue': {
    defaultKeys: 'alt+up',
    description: 'Restore queued messages to editor',
  },
  'app.thinking.cycle': {
    defaultKeys: 'shift+tab',
    description: 'Cycle thinking level',
  },
  'app.thinking.toggle': {
    defaultKeys: 'ctrl+t',
    description: 'Toggle thinking blocks visibility',
  },
  'app.model.cycleForward': {
    defaultKeys: 'ctrl+p',
    description: 'Cycle to next scoped model',
  },
  'app.model.cycleBackward': {
    defaultKeys: 'shift+ctrl+p',
    description: 'Cycle to previous scoped model',
  },
  'app.model.select': { defaultKeys: 'ctrl+l', description: 'Open model selector' },
  'app.message.copy': {
    defaultKeys: 'ctrl+x',
    description: 'Copy last assistant message to clipboard',
  },
  'app.suspend': {
    defaultKeys: process.platform === 'win32' ? [] : 'ctrl+z',
    description: 'Suspend to background (fg to resume)',
  },
  'app.editor.external': {
    defaultKeys: 'ctrl+g',
    description: 'Edit draft in external editor ($VISUAL/$EDITOR)',
  },
} as const satisfies KeybindingDefinitions;

/** 全量键位表（pi-tui 内建 + app 级）。 */
export const NOVA_KEYBINDINGS = {
  ...TUI_KEYBINDINGS,
  ...APP_KEYBINDINGS,
} as const satisfies KeybindingDefinitions;

/**
 * 保留键位清单（pi RESERVED_KEYBINDINGS_FOR_EXTENSION_CONFLICTS 对位）：
 * 编辑器全局关键动作，**第三方扩展快捷键禁覆盖**（restrictOverride）。
 * 消费者是 M4 的 TS 扩展快捷键注册——本批先把清单立起来；
 * 用户 keybindings.json 不受此限（用户对自己的键位有完全控制，pi 同款）。
 */
export const RESERVED_KEYBINDINGS: readonly string[] = [
  'app.interrupt',
  'app.clear',
  'app.exit',
  'tui.input.submit',
  'tui.select.confirm',
  'tui.select.cancel',
];
