/**
 * Semantic color palette for Nova TUI.
 *
 * All UI colors route through these tokens so themes can be swapped cleanly.
 * No raw hex literals in components.
 */

export interface ColorPalette {
  // Roles
  roleUser: string;
  roleAssistant: string;
  roleThinking: string;
  roleTool: string;

  // Text
  text: string;
  textStrong: string;
  textDim: string;
  textMuted: string;

  // Surface
  border: string;

  // State
  primary: string;
  success: string;
  error: string;
  warning: string;
}

const dark: ColorPalette = {
  roleUser: '#61afef',
  roleAssistant: '#c678dd',
  roleThinking: '#5c6370',
  roleTool: '#e5c07b',

  text: '#abb2bf',
  textStrong: '#e5c07b',
  textDim: '#5c6370',
  textMuted: '#3e4451',

  border: '#3e4450',

  primary: '#61afef',
  success: '#98c379',
  error: '#e06c75',
  warning: '#e5c07b',
};

export const defaultColors: ColorPalette = dark;

// ------------------------------------------------------------------
// Active palette — set once at app startup, read everywhere
// ------------------------------------------------------------------
let activeColors: ColorPalette = defaultColors;

export function setColors(colors: ColorPalette): void {
  activeColors = colors;
}

export function getColors(): ColorPalette {
  return activeColors;
}
