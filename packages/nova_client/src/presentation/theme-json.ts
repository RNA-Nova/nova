/**
 * 主题 JSON 契约与解析（pi theme-schema 的子集对齐）。
 *
 * 我们的必需 token 集 = pi schema 的消费面子集（核心 27 + markdown 10 +
 * syntax 9 = 46 个）；pi 主题文件（含 thinking 边框色 / export 段等
 * 多余字段）**直接可用**——多余字段容忍忽略，缺必需 token 才报错。
 *
 * ColorValue 三形态（pi 同款）：
 * - hex 字符串（``#rrggbb``）；
 * - ``""`` 空串 = 终端默认色；
 * - 0-255 整数 = 256 色索引；
 * - 其他字符串 = ``vars`` 变量引用（环引用报错）。
 */

/** 背景色 token（bgHex 生成，其余一律 hex/fg）。 */
export const BG_TOKENS: ReadonlySet<string> = new Set([
  'selectedBg',
  'userMessageBg',
  'customMessageBg',
  'toolPendingBg',
  'toolSuccessBg',
  'toolErrorBg',
]);

const CORE_TOKENS = [
  'accent',
  'border',
  'borderAccent',
  'borderMuted',
  'success',
  'error',
  'warning',
  'muted',
  'dim',
  'text',
  'thinkingText',
  'selectedBg',
  'userMessageBg',
  'userMessageText',
  'customMessageBg',
  'customMessageText',
  'customMessageLabel',
  'toolPendingBg',
  'toolSuccessBg',
  'toolErrorBg',
  'toolTitle',
  'toolOutput',
  'toolDiffAdded',
  'toolDiffRemoved',
  'toolDiffContext',
  'bashMode',
] as const;

const MARKDOWN_TOKENS = [
  'mdHeading',
  'mdLink',
  'mdLinkUrl',
  'mdCode',
  'mdCodeBlock',
  'mdCodeBlockBorder',
  'mdQuote',
  'mdQuoteBorder',
  'mdHr',
  'mdListBullet',
] as const;

const SYNTAX_TOKENS = [
  'syntaxComment',
  'syntaxKeyword',
  'syntaxFunction',
  'syntaxVariable',
  'syntaxString',
  'syntaxNumber',
  'syntaxType',
  'syntaxOperator',
  'syntaxPunctuation',
] as const;

/** 全部必需 token（46 个）。 */
export const REQUIRED_COLOR_TOKENS: readonly string[] = [
  ...CORE_TOKENS,
  ...MARKDOWN_TOKENS,
  ...SYNTAX_TOKENS,
];

export type ColorValue = string | number;

export interface ThemeJson {
  name: string;
  vars?: Record<string, ColorValue>;
  colors: Record<string, ColorValue>;
  /** HTML 导出色（可选——缺省时从 userMessageBg 派生，pi 同款语义）。 */
  export?: {
    pageBg?: ColorValue;
    cardBg?: ColorValue;
    infoBg?: ColorValue;
  };
}

function isColorValue(value: unknown): value is ColorValue {
  if (typeof value === 'string') return true;
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 255;
}

/**
 * 校验并收窄为主题 JSON（pi parseThemeJson 对位：缺 token 列出完整清单）。
 * 校验失败抛 Error（调用方收集为诊断）。
 */
export function parseThemeJson(label: string, json: unknown): ThemeJson {
  if (typeof json !== 'object' || json === null || Array.isArray(json)) {
    throw new Error(`主题 "${label}" 非法：顶层必须是对象`);
  }
  const record = json as Record<string, unknown>;
  if (typeof record.name !== 'string' || !record.name) {
    throw new Error(`主题 "${label}" 非法：缺 name 字段`);
  }
  if (record.name.includes('/')) {
    // "/" 保留（对齐 pi：将来 light/dark 自动双主题设置的语法位）
    throw new Error(`主题名 "${record.name}" 非法：不能包含 "/"`);
  }
  if (typeof record.colors !== 'object' || record.colors === null) {
    throw new Error(`主题 "${label}" 非法：缺 colors 对象`);
  }
  const colors = record.colors as Record<string, unknown>;
  const missing = REQUIRED_COLOR_TOKENS.filter((token) => !(token in colors));
  if (missing.length > 0) {
    throw new Error(
      `主题 "${label}" 缺必需色 token：\n${missing.map((t) => `  - ${t}`).join('\n')}`,
    );
  }
  const invalid = REQUIRED_COLOR_TOKENS.filter((token) => !isColorValue(colors[token]));
  if (invalid.length > 0) {
    throw new Error(
      `主题 "${label}" 存在非法色值（须为 hex 字符串 / "" / 0-255 整数 / vars 引用）：\n${invalid.map((t) => `  - ${t}`).join('\n')}`,
    );
  }
  const vars =
    typeof record.vars === 'object' && record.vars !== null
      ? (record.vars as Record<string, ColorValue>)
      : undefined;
  const exportSection =
    typeof record.export === 'object' && record.export !== null
      ? (record.export as ThemeJson['export'])
      : undefined;
  return {
    name: record.name,
    vars,
    colors: colors as Record<string, ColorValue>,
    export: exportSection,
  };
}

/** vars 引用解析（环引用报错；对齐 pi resolveVarRefs）。 */
function resolveVarRefs(
  value: ColorValue,
  vars: Record<string, ColorValue>,
  visited: Set<string>,
): string | number {
  if (typeof value === 'number' || value === '' || value.startsWith('#')) return value;
  if (visited.has(value)) {
    throw new Error(`vars 环引用：${[...visited, value].join(' → ')}`);
  }
  if (!(value in vars)) {
    throw new Error(`vars 引用未定义：${value}`);
  }
  visited.add(value);
  return resolveVarRefs(vars[value]!, vars, visited);
}

/** 主题全部色值解析为最终形态（hex / "" / 256 索引）。 */
export function resolveThemeColors(json: ThemeJson): Record<string, string | number> {
  const resolved: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(json.colors)) {
    resolved[key] = resolveVarRefs(value, json.vars ?? {}, new Set());
  }
  return resolved;
}
