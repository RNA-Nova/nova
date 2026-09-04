/**
 * 内建 dark 主题（"好看"来自人家的调色板）。
 *
 * 内联 ts 而非运行时读 json：避免 tsc 不复制 json 到 dist 的构建/打包
 * 问题（npm files 不含 src 时运行时路径即失效）。用户自定义主题走
 * frontend/tui/themes/*.json 文件通道（前后端分治 §9 前端域）。
 */

import type { ThemeJson } from './theme-json.js';

export const BUILTIN_DARK: ThemeJson = {
  name: 'dark',
  vars: {
    cyan: '#00d7ff',
    blue: '#5f87ff',
    green: '#b5bd68',
    red: '#cc6666',
    yellow: '#ffff00',
    text: '#d4d4d4',
    gray: '#808080',
    dimGray: '#666666',
    darkGray: '#505050',
    accent: '#8abeb7',
    selectedBg: '#3a3a4a',
    userMsgBg: '#343541',
    toolPendingBg: '#282832',
    toolSuccessBg: '#283228',
    toolErrorBg: '#3c2828',
    customMsgBg: '#2d2838',
  },
  colors: {
    accent: 'accent',
    border: 'blue',
    borderAccent: 'cyan',
    borderMuted: 'darkGray',
    success: 'green',
    error: 'red',
    warning: 'yellow',
    muted: 'gray',
    dim: 'dimGray',
    text: 'text',
    thinkingText: 'gray',

    selectedBg: 'selectedBg',
    userMessageBg: 'userMsgBg',
    userMessageText: 'text',
    customMessageBg: 'customMsgBg',
    customMessageText: 'text',
    customMessageLabel: '#9575cd',
    toolPendingBg: 'toolPendingBg',
    toolSuccessBg: 'toolSuccessBg',
    toolErrorBg: 'toolErrorBg',
    toolTitle: 'text',
    toolOutput: 'gray',

    mdHeading: '#f0c674',
    mdLink: '#81a2be',
    mdLinkUrl: 'dimGray',
    mdCode: 'accent',
    mdCodeBlock: 'green',
    mdCodeBlockBorder: 'gray',
    mdQuote: 'gray',
    mdQuoteBorder: 'gray',
    mdHr: 'gray',
    mdListBullet: 'accent',

    toolDiffAdded: 'green',
    toolDiffRemoved: 'red',
    toolDiffContext: 'gray',

    syntaxComment: '#6A9955',
    syntaxKeyword: '#569CD6',
    syntaxFunction: '#DCDCAA',
    syntaxVariable: '#9CDCFE',
    syntaxString: '#CE9178',
    syntaxNumber: '#B5CEA8',
    syntaxType: '#4EC9B0',
    syntaxOperator: '#D4D4D4',
    syntaxPunctuation: '#D4D4D4',

    // thinking 级别边框色（可选 token——自定义主题缺失时回退 borderMuted）
    thinkingOff: 'darkGray',
    thinkingMinimal: '#6e6e6e',
    thinkingLow: '#5f87af',
    thinkingMedium: '#81a2be',
    thinkingHigh: '#b294bb',
    thinkingXhigh: '#d183e8',
    thinkingMax: '#ff5fff',

    bashMode: 'green',
  },
  export: {
    pageBg: '#18181e',
    cardBg: '#1e1e24',
    infoBg: '#3c3728',
  },
};
