/**
 * 键位提示格式化（pi keybinding-hints.ts 对位）。
 *
 * 从全局键位表动态生成提示文本（"↑↓ navigate  enter select  esc cancel"）——
 * 键位可配置后提示自动跟随（不写死）。
 */

import { getKeybindings, type Keybinding, type KeyId } from '@earendil-works/pi-tui';

import { colors } from '../../themes/index.js';

function formatKeyText(key: string, capitalize = false): string {
  return key
    .split('/')
    .map((k) =>
      k
        .split('+')
        .map((part) => {
          const display =
            process.platform === 'darwin' && part.toLowerCase() === 'alt' ? 'option' : part;
          return capitalize ? display.charAt(0).toUpperCase() + display.slice(1) : display;
        })
        .join('+'),
    )
    .join('/');
}

/** 键位表键 → 键文本（"enter" / "ctrl+c" / "tab/shift+tab"）。 */
export function keyText(keybinding: Keybinding): string {
  const keys: KeyId[] = getKeybindings().getKeys(keybinding);
  if (keys.length === 0) return '';
  return formatKeyText(keys.join('/'));
}

/** 键位 + 描述的提示片段（dim 键 + muted 描述）。 */
export function keyHint(keybinding: Keybinding, description: string): string {
  return colors.dim(keyText(keybinding)) + colors.muted(` ${description}`);
}

/** 原始键名 + 描述（不走键位表，如 ↑↓）。 */
export function rawKeyHint(key: string, description: string): string {
  return colors.dim(formatKeyText(key)) + colors.muted(` ${description}`);
}
