/**
 * 主题 JSON 契约（已上移至 ``presentation/theme-json.ts``——纯数据契约归
 * 呈现契约层，loader（宿主无关）需要它校验包内主题资产）。
 * 本文件仅为既有 TUI 侧 import 的兼容出口。
 */

export {
  BG_TOKENS,
  REQUIRED_COLOR_TOKENS,
  parseThemeJson,
  resolveThemeColors,
  type ColorValue,
  type ThemeJson,
} from '../../../presentation/theme-json.js';
