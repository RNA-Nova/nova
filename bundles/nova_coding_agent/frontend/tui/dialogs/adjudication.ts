/**
 * adjudication 审批对话框（dialog:adjudication——裁决/审批流的专用框，
 * 对位 codex TUI 的 exec 审批视图）。
 *
 * 契约：
 * - 入参 params：{ title, command?, reason?, proposeAmendment? }；
 * - done({decision: "once" | "session" | "forever" | "deny"})；
 *   done(undefined) = 取消（后端按拒绝处理，与 select 降级语义一致）。
 *
 * 选项（proposeAmendment=false 时无"永远允许"）：
 *   允许一次(y) / 本会话都允许(s) / 永远允许（写入规则）(a) / 拒绝(n)；
 * 键位：↑↓ 移动 · enter 提交 · y/s/a/n 直达 · esc 取消。
 * 视觉：复用面板惯用法（─ 边框 + › 光标行），命令经等宽块呈现；配色经
 * 工厂 env 注入（RegionEnv.colors）。
 */
import {
  Key,
  matchesKey,
  visibleWidth,
  wrapTextWithAnsi,
  type Component,
  type Focusable,
} from '@earendil-works/pi-tui';

import { colors as themeColors } from 'nova-client/modes/tui/themes/index';

export type AdjudicationDecision = 'once' | 'session' | 'forever' | 'deny';

export interface AdjudicationOption {
  decision: AdjudicationDecision;
  label: string;
  hotkey: string;
}

type DialogColors = typeof themeColors;

/** 按 proposeAmendment 三态组装选项表（顺序即展示序）。 */
export function adjudicationOptions(proposeAmendment: boolean): AdjudicationOption[] {
  const options: AdjudicationOption[] = [
    { decision: 'once', label: '允许一次', hotkey: 'y' },
    { decision: 'session', label: '本会话都允许', hotkey: 's' },
  ];
  if (proposeAmendment) {
    options.push({ decision: 'forever', label: '永远允许（写入规则）', hotkey: 'a' });
  }
  options.push({ decision: 'deny', label: '拒绝', hotkey: 'n' });
  return options;
}

/** 审批对话框（光标列表 + 热键直达）。 */
export class AdjudicationDialog implements Component, Focusable {
  private selectedIndex = 0;
  private cachedWidth?: number;
  private cachedLines?: string[];
  private _focused = false;

  constructor(
    private readonly title: string,
    private readonly command: string | null,
    private readonly reason: string | null,
    private readonly options: AdjudicationOption[],
    private readonly colors: DialogColors,
    private readonly onDone: (result?: { decision: AdjudicationDecision }) => void,
  ) {}

  get focused(): boolean {
    return this._focused;
  }

  set focused(value: boolean) {
    this._focused = value;
  }

  private refresh(): void {
    this.cachedLines = undefined;
    this.cachedWidth = undefined;
  }

  invalidate(): void {
    this.refresh();
  }

  handleInput(data: string): void {
    if (matchesKey(data, Key.up)) {
      this.selectedIndex = Math.max(0, this.selectedIndex - 1);
      this.refresh();
      return;
    }
    if (matchesKey(data, Key.down)) {
      this.selectedIndex = Math.min(this.options.length - 1, this.selectedIndex + 1);
      this.refresh();
      return;
    }
    if (matchesKey(data, Key.enter)) {
      this.onDone({ decision: this.options[this.selectedIndex].decision });
      return;
    }
    if (matchesKey(data, Key.escape)) {
      this.onDone(undefined);
      return;
    }
    // 热键直达（y/s/a/n）
    const hit = this.options.find((o) => o.hotkey === data);
    if (hit) {
      this.onDone({ decision: hit.decision });
    }
  }

  render(width: number): string[] {
    if (this.cachedLines && this.cachedWidth === width) return this.cachedLines;

    const lines: string[] = [];
    const renderWidth = Math.max(1, width);
    const colors = this.colors;

    const addWrappedWithPrefix = (prefix: string, text: string) => {
      const prefixWidth = visibleWidth(prefix);
      if (prefixWidth >= renderWidth) {
        lines.push(...wrapTextWithAnsi(prefix + text, renderWidth));
        return;
      }
      const wrapped = wrapTextWithAnsi(text, renderWidth - prefixWidth);
      const continuationPrefix = ' '.repeat(prefixWidth);
      for (let i = 0; i < wrapped.length; i++) {
        lines.push(`${i === 0 ? prefix : continuationPrefix}${wrapped[i]}`);
      }
    };

    lines.push(colors.accent('─'.repeat(renderWidth)));
    addWrappedWithPrefix(' ', colors.warning(`⚠ ${this.title}`));
    lines.push('');

    if (this.command) {
      for (const line of wrapTextWithAnsi(this.command, Math.max(1, renderWidth - 2))) {
        lines.push(`  ${colors.muted(line)}`);
      }
      lines.push('');
    }
    if (this.reason) {
      addWrappedWithPrefix(' ', colors.muted(`原因: ${this.reason}`));
      lines.push('');
    }

    for (let i = 0; i < this.options.length; i++) {
      const option = this.options[i];
      const selected = i === this.selectedIndex;
      const line = `${option.label}  ${colors.dim(`(${option.hotkey})`)}`;
      addWrappedWithPrefix(selected ? colors.accent('› ') : '  ', selected ? colors.accent(line) : line);
    }

    lines.push('');
    addWrappedWithPrefix(' ', colors.dim('↑↓ 移动 · enter 提交 · 字母直达 · esc 取消'));
    lines.push(colors.accent('─'.repeat(renderWidth)));

    this.cachedWidth = width;
    this.cachedLines = lines;
    return lines;
  }
}

/** dialog:adjudication 工厂（ExtensionUIAPI.registerDialog 的注册形态）。 */
export function adjudicationDialogFactory(
  env: unknown,
  params: Record<string, unknown>,
  done: (result?: unknown) => void,
): Component {
  const colors = (env as { colors?: DialogColors }).colors ?? themeColors;
  const title = typeof params.title === 'string' && params.title ? params.title : '审批请求';
  const command = typeof params.command === 'string' && params.command ? params.command : null;
  const reason = typeof params.reason === 'string' && params.reason ? params.reason : null;
  const options = adjudicationOptions(params.proposeAmendment === true);
  return new AdjudicationDialog(title, command, reason, options, colors, (result) => done(result));
}
