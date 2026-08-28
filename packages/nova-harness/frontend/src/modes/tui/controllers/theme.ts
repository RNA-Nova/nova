/**
 * ThemeController：主题初始化 + /theme 选择器 + 持久化。
 *
 * - 初始化：runtime.start 后读 settings.theme 初始化主题（无设置 →
 *   COLORFGBG 终端背景检测兜底）；自定义主题目录诊断经 transcript 提示；
 * - ``/theme``（前端本地命令，不进后端——pi 同款：主题是纯前端关心的事）：
 *   SearchableSelector + onHighlight **移动即预览**（全量重渲），Enter
 *   确认并持久化（updateSettings），Esc 恢复打开前的主题；
 * - 切换渲染：onThemeChange（装配根接：transcript.rebuildAll + 重渲）。
 */

import type { NovaUIRuntime } from 'nova-client';

import { SearchableSelector, type SearchableItem } from '../components/pickers/searchable.js';
import {
  getAvailableThemes,
  getCurrentThemeName,
  initTheme,
  setTheme,
} from '../themes/index.js';
import type { DialogController } from './dialogs.js';
import type { TranscriptController } from './transcript.js';

export class ThemeController {
  constructor(
    private readonly runtime: NovaUIRuntime,
    private readonly dialogs: DialogController,
    private readonly transcript: TranscriptController,
  ) {}

  /** 启动初始化：settings.theme（装配根统一读取后传入）→ initTheme；诊断与回退提示进转录。 */
  async init(configured?: string): Promise<void> {
    initTheme(configured);
    if (configured && getCurrentThemeName() !== configured) {
      this.transcript.addInfo(`主题 "${configured}" 加载失败，已回退 dark`);
    }
    const { diagnostics } = getAvailableThemes();
    for (const diagnostic of diagnostics) {
      this.transcript.addInfo(`themes: ${diagnostic}`);
    }
  }

  /** /theme 命令入口：主题选择器（预览 → 确认持久化 / 取消恢复）。 */
  openSelector(): void {
    if (this.dialogs.isActive) return; // 有框不叠加
    const original = getCurrentThemeName();
    const { themes, diagnostics } = getAvailableThemes();
    for (const diagnostic of diagnostics) {
      this.transcript.addInfo(`themes: ${diagnostic}`);
    }
    // 当前主题排第一（打开即高亮现状；其余按名称序）；automatic（跟随终端）固定首项
    const items: SearchableItem[] = [
      { value: 'automatic', label: 'automatic', description: '跟随终端配色（内建 dark/light 自动切换）' },
      ...themes
        .map((theme) => ({
          value: theme.name,
          label: theme.name,
          description: theme.source === 'builtin' ? '内建' : theme.source,
        }))
        .sort((a, b) =>
          a.value === original ? -1 : b.value === original ? 1 : a.value.localeCompare(b.value),
        ),
    ];

    const selector = new SearchableSelector(
      '主题',
      items,
      {
        onSelect: (name) => {
          this.dialogs.restoreLocal();
          this.applyTheme(name);
        },
        onCancel: () => {
          setTheme(original); // 恢复打开前的主题（预览撤销）
          this.dialogs.restoreLocal();
        },
        onHighlight: (name) => {
          setTheme(name); // 移动即预览（重渲由 onThemeChange 驱动）
        },
      },
      { placeholder: '输入过滤主题名' },
    );
    this.dialogs.showLocal(selector, selector);
  }

  /** 确认：应用 + 持久化到 settings（失败提示，已回退 dark）。/settings 的主题项复用。 */
  applyTheme(name: string): void {
    const result = setTheme(name);
    if (!result.success) {
      this.transcript.addError(new Error(`主题 "${name}" 加载失败：${result.error}（已回退 dark）`));
      return;
    }
    void this.runtime
      .invoke('updateSettings', { settings: { theme: name } })
      .catch((error: unknown) => this.transcript.addError(error));
  }
}
