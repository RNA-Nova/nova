/**
 * SettingsController：/settings 可视化编辑（pi settings-selector 对位）。
 *
 * 两级选择器（前端本地交互）：
 * - 第一级：配置项列表（label + 当前值）；
 * - 第二级：候选值列表（当前值排第一），选中即生效+持久化，Esc 返回上级。
 *
 * 生效语义：currentSettings 原地更新（双 Esc 等 getter 现取即新值）+
 * 项级 onChange 即时动作（theme 切换/编辑器参数即时应用）+ 持久化。
 *
 * 持久化三通道（SettingItem.persist 优先，缺省 updateSettings）：
 * - 后端 settings.json：updateSettings RPC（quietStartup 等老项 + defaultProjectTrust——线上 camel 键）；
 * - 会话态：RPC 方法项（setSteeringMode/setFollowUpMode/setThinkingLevel/
 *   setAutoCompactionEnabled——当前值读快照，不写 settings 缓存）；
 * - 前端 settings.json（前端域）：runtime.uiSettings（tree_filter_mode 等纯前端项，
 *   键注册与 getter 归 utils/tui-settings.ts）。
 */

import type { NovaUIRuntime, SessionSnapshot } from 'nova-tui';

import { SearchableSelector, type SearchableItem } from '../components/pickers/searchable.js';
import { getAvailableThemes, getCurrentThemeName } from '../themes/index.js';
import {
  TREE_FILTER_MODES,
  getAutocompleteMaxItems,
  getEditorPadding,
  getTreeFilterMode,
  initTuiSettings,
  isBranchSummarySkipPrompt,
  isClearOnShrink,
  isDesktopNotifyEnabled,
  isTerminalProgressEnabled,
  setTuiSetting,
} from '../utils/tui-settings.js';
import { applyFrontendSettings, clearTerminalProgress } from './terminal.js';
import type { DialogController } from './dialogs.js';
import type { ThemeController } from './theme.js';
import type { TranscriptController } from './transcript.js';

interface SettingValueOption {
  value: string;
  label: string;
}

interface SettingItem {
  /** settings 键（snake_case——与后端 Settings 模型字段一致；前端项同形进 ui-settings）。 */
  id: string;
  label: string;
  description: string;
  /** 当前值显示（第一级描述列）。 */
  currentValue: () => string;
  /** 候选值（可动态——theme 列主题清单、thinking_level 列快照级别表）。 */
  options: () => SettingValueOption[];
  /** 选中即时动作（theme 切换/编辑器参数应用等）；持久化由控制器统一做。 */
  onChange?: (value: string) => void;
  /** 值解析（布尔项 'true'/'false' → boolean、数字项 → number）；缺省原样字符串。 */
  parse?: (value: string) => unknown;
  /** 跳过统一持久化（theme 项经 ThemeController 自持久化）。 */
  skipPersist?: boolean;
  /** 自定义持久化（会话态 RPC 项 / 前端 ui-settings 项）——替代默认 updateSettings。 */
  persist?: (parsed: unknown) => void;
  /** 不写 currentSettings 缓存（缓存只镜像后端 settings.json——会话态/前端项不入）。 */
  skipCache?: boolean;
  /** 候选为空时的禁用说明（thinking_level 模型不支持）——不开第二级，仅提示。 */
  emptyNote?: string;
  /** 生效说明（如"下次启动生效"）——选中后提示。 */
  note?: string;
}

const BOOL_OPTIONS: SettingValueOption[] = [
  { value: 'true', label: 'true（开）' },
  { value: 'false', label: 'false（关）' },
];
const parseBool = (value: string): unknown => value === 'true';
const parseIntOption = (value: string): unknown => Number.parseInt(value, 10);

const MODE_OPTIONS: SettingValueOption[] = [
  { value: 'all', label: 'all（全部注入）' },
  { value: 'one-at-a-time', label: 'one-at-a-time（逐条）' },
];

export class SettingsController {
  constructor(
    private readonly runtime: NovaUIRuntime,
    private readonly dialogs: DialogController,
    private readonly transcript: TranscriptController,
    private readonly theme: ThemeController,
    /** 装配根的 settings 缓存（原地更新——getter 消费方即时生效）。 */
    private readonly currentSettings: Record<string, unknown>,
  ) {
    // 前端设置键注册兜底（装配根已 initTuiSettings 则同 owner 幂等重载）
    const uiSettings = (this.runtime as { uiSettings?: unknown }).uiSettings;
    if (uiSettings !== undefined) initTuiSettings(uiSettings as Parameters<typeof initTuiSettings>[0]);
  }

  /** 会话快照（会话态设置项的当前值来源——RPC 成功后经事件刷新）。 */
  private snapshot(): SessionSnapshot | undefined {
    return (this.runtime as { store?: { currentSnapshot?: SessionSnapshot | null } }).store
      ?.currentSnapshot ?? undefined;
  }

  /**
   * RPC 方法项持久化（结果不经 updateSettings——会话态由后端广播刷新）。
   * 类型化 invoke（契约含 setSteeringMode/setFollowUpMode/setAutoCompactionEnabled）。
   */
  private persistRpc<
    M extends 'setSteeringMode' | 'setFollowUpMode' | 'setAutoCompactionEnabled' | 'setThinkingLevel',
  >(
    method: M,
    params: Record<string, unknown>,
  ): void {
    void (this.runtime.invoke as (m: string, p?: unknown) => Promise<unknown>)(method, params).catch(
      (error: unknown) => this.transcript.addError(error),
    );
  }

  private settingItems(): SettingItem[] {
    return [
      {
        id: 'theme',
        label: '主题',
        description: '界面配色（选中即预览生效；automatic 跟随终端亮暗）',
        currentValue: () => getCurrentThemeName(),
        options: () => [
          { value: 'automatic', label: 'automatic（跟随终端亮暗）' },
          ...getAvailableThemes().themes.map((theme) => ({
            value: theme.name,
            label: theme.name,
          })),
        ],
        onChange: (value) => this.theme.applyTheme(value),
        skipPersist: true, // applyTheme 内已持久化
      },
      {
        id: 'doubleEscapeAction',
        label: '双 Esc 导航',
        description: '空编辑器双击 Esc 触发（tree=会话树 / fork=消息分叉 / none=关闭）',
        currentValue: () => String(this.currentSettings.doubleEscapeAction ?? 'tree'),
        options: () => [
          { value: 'tree', label: 'tree（会话树）' },
          { value: 'fork', label: 'fork（消息分叉）' },
          { value: 'none', label: 'none（关闭）' },
        ],
      },
      {
        id: 'quietStartup',
        label: '安静启动',
        description: '启动时不显示 welcome 区',
        currentValue: () => String(this.currentSettings.quietStartup ?? false),
        options: () => BOOL_OPTIONS,
        parse: parseBool,
        note: '下次启动生效',
      },
      {
        id: 'hideThinkingBlock',
        label: '隐藏 thinking 块',
        description: 'thinking 全文折叠为静态标签（ctrl+t 即时切换同效）',
        currentValue: () => String(this.currentSettings.hideThinkingBlock ?? false),
        options: () => BOOL_OPTIONS,
        parse: parseBool,
      },
      {
        id: 'showCacheMissNotices',
        label: '缓存 miss 提醒',
        description: 'transcript 显示显著的 prompt 缓存 miss 提醒',
        currentValue: () => String(this.currentSettings.showCacheMissNotices ?? false),
        options: () => BOOL_OPTIONS,
        parse: parseBool,
      },
      {
        id: 'steering_mode',
        label: 'steering 模式',
        description: '运行中插话注入策略（会话态——即时生效）',
        currentValue: () => this.snapshot()?.steeringMode ?? 'all',
        options: () => MODE_OPTIONS,
        skipCache: true,
        persist: (parsed) => this.persistRpc('setSteeringMode', { mode: parsed }),
      },
      {
        id: 'followup_mode',
        label: 'follow-up 模式',
        description: 'follow-up 排队消息注入策略（会话态——即时生效）',
        currentValue: () => this.snapshot()?.followUpMode ?? 'all',
        options: () => MODE_OPTIONS,
        skipCache: true,
        persist: (parsed) => this.persistRpc('setFollowUpMode', { mode: parsed }),
      },
      {
        id: 'auto_compaction',
        label: '自动压缩',
        description: '上下文接近上限时自动 compaction（会话态）',
        currentValue: () => String(this.snapshot()?.autoCompactionEnabled ?? true),
        options: () => BOOL_OPTIONS,
        parse: parseBool,
        skipCache: true,
        persist: (parsed) => this.persistRpc('setAutoCompactionEnabled', { enabled: parsed }),
      },
      {
        id: 'thinking_level',
        label: 'thinking 级别',
        description: '模型思考深度（off/minimal/low/medium/high/xhigh/max）',
        currentValue: () => this.snapshot()?.thinkingLevel ?? 'off',
        options: () =>
          (this.snapshot()?.availableThinkingLevels ?? []).map((level) => ({
            value: level,
            label: level,
          })),
        emptyNote: '当前模型不支持 thinking 级别调整',
        skipCache: true,
        persist: (parsed) => this.persistRpc('setThinkingLevel', { level: parsed }),
      },
      {
        id: 'defaultProjectTrust',
        label: '默认项目信任',
        description: '项目目录 trust 门默认裁决（ask=每次询问 / always=总是信任 / never=总是拒绝）',
        currentValue: () => String(this.currentSettings.defaultProjectTrust ?? 'ask'),
        options: () => [
          { value: 'ask', label: 'ask（每次询问）' },
          { value: 'always', label: 'always（总是信任）' },
          { value: 'never', label: 'never（总是拒绝）' },
        ],
        note: '新会话生效',
      },
      {
        id: 'roleBoundary',
        label: '角色边界',
        description: 'open=yaml 名单只做初始激活集（面板可见全池）/ strict=yaml 是注册表硬边界',
        currentValue: () => String(this.currentSettings.roleBoundary ?? 'open'),
        options: () => [
          { value: 'open', label: 'open（默认，运行时可选 yaml 外工具）' },
          { value: 'strict', label: 'strict（角色名单即边界，不可超配）' },
        ],
        note: '即时生效（下次工具注册表刷新起）',
      },
      {
        id: 'tree_filter_mode',
        label: '会话树过滤器',
        description: '/tree 选择器初始过滤模式（选择器内可临时切换，不回写）',
        currentValue: () => getTreeFilterMode(),
        options: () =>
          TREE_FILTER_MODES.map((mode) => ({
            value: mode,
            label: `${mode}（${TREE_FILTER_MODE_LABELS[mode]}）`,
          })),
        skipCache: true,
        persist: (parsed) => void setTuiSetting('tree_filter_mode', parsed),
      },
      {
        id: 'branch_summary_skip_prompt',
        label: '分支摘要免确认',
        description: 'navigateTree 分支摘要跳过确认提示直接执行',
        currentValue: () => String(isBranchSummarySkipPrompt()),
        options: () => BOOL_OPTIONS,
        parse: parseBool,
        skipCache: true,
        persist: (parsed) => void setTuiSetting('branch_summary_skip_prompt', parsed),
      },
      {
        id: 'editor_padding',
        label: '编辑器内边距',
        description: '输入框左右留白列数（0-3，即时生效）',
        currentValue: () => String(getEditorPadding()),
        options: () => ['0', '1', '2', '3'].map((v) => ({ value: v, label: v })),
        parse: parseIntOption,
        skipCache: true,
        persist: (parsed) => void setTuiSetting('editor_padding', parsed),
        onChange: () => applyFrontendSettings(),
      },
      {
        id: 'autocomplete_max_items',
        label: '补全可见条数',
        description: '编辑器补全下拉最大可见行（即时生效）',
        currentValue: () => String(getAutocompleteMaxItems()),
        options: () => ['3', '5', '7', '10', '15', '20'].map((v) => ({ value: v, label: v })),
        parse: parseIntOption,
        skipCache: true,
        persist: (parsed) => void setTuiSetting('autocomplete_max_items', parsed),
        onChange: () => applyFrontendSettings(),
      },
      {
        id: 'clear_on_shrink',
        label: '收缩清屏',
        description: '内容收缩时清空残余行（关闭可减少慢终端重绘）',
        currentValue: () => String(isClearOnShrink()),
        options: () => BOOL_OPTIONS,
        parse: parseBool,
        skipCache: true,
        persist: (parsed) => void setTuiSetting('clear_on_shrink', parsed),
        onChange: () => applyFrontendSettings(),
      },
      {
        id: 'terminal_progress',
        label: '终端进度',
        description: 'OSC 9;4 终端进度指示（working/compacting 时置位）',
        currentValue: () => String(isTerminalProgressEnabled()),
        options: () => BOOL_OPTIONS,
        parse: parseBool,
        skipCache: true,
        persist: (parsed) => void setTuiSetting('terminal_progress', parsed),
        onChange: () => {
          if (!isTerminalProgressEnabled()) clearTerminalProgress(); // 关闭即清残留进度
        },
      },
      {
        id: 'desktop_notify',
        label: '桌面通知',
        description: 'agent 回复完成时发送终端桌面通知（OSC 9/777/99）',
        currentValue: () => String(isDesktopNotifyEnabled()),
        options: () => BOOL_OPTIONS,
        parse: parseBool,
        skipCache: true,
        persist: (parsed) => void setTuiSetting('desktop_notify', parsed),
      },
    ];
  }

  /** /settings 入口：第一级配置项列表（本地框重入合法——第二级 Esc 返回经此）。 */
  openSelector(): void {
    if (this.dialogs.isActive && !this.dialogs.hasLocalDialog) return; // RPC 对话框不叠加
    const items: SearchableItem[] = this.settingItems().map((item) => ({
      value: item.id,
      label: item.label,
      description: `${item.currentValue()} — ${item.description}`,
    }));
    const selector = new SearchableSelector(
      '设置',
      items,
      {
        onSelect: (id) => {
          const item = this.settingItems().find((candidate) => candidate.id === id);
          if (!item) return;
          if (item.options().length === 0) {
            // 禁用态（thinking_level 模型不支持等）——不开第二级，仅提示
            this.transcript.addInfo(item.emptyNote ?? `${item.label}：当前无可选值`);
            return;
          }
          this.openValueSelector(item);
        },
        onCancel: () => this.dialogs.restoreLocal(),
      },
      { placeholder: '输入过滤设置项' },
    );
    this.dialogs.showLocal(selector, selector);
  }

  /** 第二级：候选值列表（当前值排第一；Esc 返回第一级）。 */
  private openValueSelector(item: SettingItem): void {
    const current = item.currentValue();
    const items: SearchableItem[] = item.options()
      .map((option) => ({
        value: option.value,
        label: option.label,
        description: option.value === current ? '当前' : undefined,
      }))
      .sort((a, b) =>
        a.value === current ? -1 : b.value === current ? 1 : a.value.localeCompare(b.value),
      );
    const selector = new SearchableSelector(
      `${item.label}`,
      items,
      {
        onSelect: (value) => {
          this.dialogs.restoreLocal();
          this.apply(item, value);
        },
        onCancel: () => this.openSelector(), // Esc 返回第一级
      },
      { placeholder: item.description },
    );
    // 替换第一级框（同级槽位换人，不经 restoreLocal——避免闪回编辑器）
    this.dialogs.showLocal(selector, selector);
  }

  /** 生效：currentSettings 原地更新（skipCache 项除外）+ 即时动作 + 持久化。 */
  private apply(item: SettingItem, value: string): void {
    const parsed = item.parse ? item.parse(value) : value;
    if (item.skipCache !== true) this.currentSettings[item.id] = parsed;
    item.onChange?.(value);
    if (item.persist !== undefined) {
      item.persist(parsed);
    } else if (item.skipPersist !== true) {
      void this.runtime
        .invoke('updateSettings', { settings: { [item.id]: parsed } })
        .catch((error: unknown) => this.transcript.addError(error));
    }
    this.transcript.addInfo(
      `${item.label} → ${value}${item.note ? `（${item.note}）` : ''}`,
    );
  }
}

/** 会话树过滤模式的中文说明（设置面板选项标签用）。 */
const TREE_FILTER_MODE_LABELS: Record<(typeof TREE_FILTER_MODES)[number], string> = {
  default: '默认',
  'no-tools': '隐藏工具',
  'user-only': '仅用户消息',
  'labeled-only': '仅带标签',
  all: '全部',
};
