/**
 * Pi-tui theme adapters — generate MarkdownTheme and EditorTheme
 * from our semantic ColorPalette.
 *
 * Markdown code blocks emit special markers so that AssistantMessageComponent
 * can post-process them into full box-drawing borders.
 */

import type { EditorTheme, MarkdownTheme } from '@earendil-works/pi-tui';
import chalk from 'chalk';

import type { ColorPalette } from './colors.js';

// Markers used by AssistantMessageComponent to draw full borders around code blocks.
export const CB_TOP_PREFIX = '\u0007CBTOP:';
export const CB_BOT_MARKER = '\u0007CBBOT\u0007';
export const CB_LINE_PREFIX = '\u0007CBLINE\u0007';

export interface MarkdownThemeInternal extends MarkdownTheme {
  /** Internal counter used to pair opening/closing ``` fences. */
  _fenceCount: number;
}

export function createMarkdownTheme(colors: ColorPalette): MarkdownThemeInternal {
  const muted = chalk.hex(colors.textDim);
  const theme: MarkdownThemeInternal = {
    heading: (s: string) => chalk.hex(colors.textStrong).bold(s),
    bold: (s: string) => chalk.bold(s),
    italic: (s: string) => chalk.italic(s),
    code: (s: string) => chalk.hex(colors.success)(s),
    codeBlock: (s: string) => `${CB_LINE_PREFIX}${chalk.hex(colors.success)(s)}`,
    codeBlockBorder: function (this: MarkdownThemeInternal, s: string) {
      if (s.startsWith('```')) {
        this._fenceCount++;
        if (this._fenceCount % 2 === 1) {
          const lang = s.length > 3 ? s.slice(3).trim() : '';
          return `${CB_TOP_PREFIX}${lang}\u0007`;
        }
        return CB_BOT_MARKER;
      }
      return s;
    },
    quote: (s: string) => muted(`│ ${s}`),
    quoteBorder: (s: string) => muted(s),
    hr: (s: string) => chalk.hex(colors.border)(s),
    listBullet: (s: string) => chalk.hex(colors.text)(s),
    link: (s: string) => chalk.hex(colors.primary).underline(s),
    linkUrl: (s: string) => chalk.hex(colors.primary).underline(s),
    strikethrough: (s: string) => chalk.strikethrough(s),
    underline: (s: string) => chalk.underline(s),
    codeBlockIndent: '', // we draw our own full border; no indent needed
    _fenceCount: 0,
  };
  return theme;
}

export function createEditorTheme(colors: ColorPalette): EditorTheme {
  const muted = chalk.hex(colors.textDim);
  return {
    borderColor: (s: string) => chalk.hex(colors.border)(s),
    selectList: {
      selectedPrefix: (s: string) => chalk.inverse(s),
      selectedText: (s: string) => chalk.inverse(s),
      description: (s: string) => muted(s),
      scrollInfo: (s: string) => muted(s),
      noMatch: (s: string) => muted(s),
    },
  };
}
