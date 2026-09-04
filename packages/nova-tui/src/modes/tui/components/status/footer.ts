/**
 * FooterView（三行状态栏，数据源走 RPC/快照/Node 本地）。
 *
 * 行 1：~cwd (git branch) • 会话名
 * 行 2：↑input ↓output R缓存读 W缓存写 · 上下文用量% · model · thinking
 * （token 成本/cache_waste 归后续切片；git branch 用 Node 本地读 .git/HEAD
 * 并 watch 变化。）
 */

import chalk from 'chalk';
import { watchFile, unwatchFile } from 'node:fs';
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import {
  guardComponentLineWidth,
  regionSlot,
  type NovaBlock,
  type NovaUIRuntime,
  type RegionContext,
} from 'nova-tui';
import { truncateToWidth, type Component } from '@earendil-works/pi-tui';

import { blocksToComponents } from '../../blocks/index.js';
import { colors } from '../../themes/index.js';

/** 从 cwd 向上找 .git 目录，读当前分支（detach 时返回短 hash）。 */
function readGitBranch(cwd: string): string | undefined {
  let dir = cwd;
  for (;;) {
    const gitHead = join(dir, '.git', 'HEAD');
    if (existsSync(gitHead)) {
      try {
        const head = readFileSync(gitHead, 'utf8').trim();
        if (head.startsWith('ref:')) {
          return head.split('/').pop() ?? undefined;
        }
        return head.slice(0, 8) || undefined;
      } catch {
        return undefined;
      }
    }
    const parent = dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

export class FooterView implements Component {
  private branch: string | undefined;
  private stats:
    | {
        input: number;
        output: number;
        cacheRead: number;
        cacheWrite: number;
        cost: number;
      }
    | undefined;
  /** 上下文用量（getContextUsage：percent/tokens/context_window）。 */
  private contextUsage: { tokens: number; contextWindow: number; percent: number } | undefined;
  private readonly watchPath: string | undefined;
  /** region 部件组件缓存（指纹比对——producer 输出不变不重建组件）。 */
  private regionComponents: Component[] | undefined;
  private regionFingerprint: string | undefined;

  constructor(
    private readonly runtime: NovaUIRuntime,
    private readonly cwd: string,
    /** settings 现取（auto-compact 标记——装配根的 currentSettings 引用）。 */
    private readonly settings: () => Record<string, unknown> = () => ({}),
  ) {
    this.branch = readGitBranch(cwd);
    // git branch watch（500ms 去抖由 watchFile 的 interval 承担）
    let dir = cwd;
    for (;;) {
      const candidate = join(dir, '.git', 'HEAD');
      if (existsSync(candidate)) {
        this.watchPath = candidate;
        break;
      }
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
    if (this.watchPath) {
      watchFile(this.watchPath, { interval: 500 }, () => {
        this.branch = readGitBranch(cwd);
        // render 每帧现算，无需主动触发（TUI 60fps 节流）
      });
    }
  }

  dispose(): void {
    if (this.watchPath) unwatchFile(this.watchPath);
  }

  /** turn 结束后调用：刷新 token 统计与上下文用量（stats 只在此时刻变化）。 */
  async refreshStats(): Promise<void> {
    try {
      const [statsResult, usageResult] = await Promise.all([
        this.runtime.invoke('getSessionStats'),
        this.runtime.invoke('getContextUsage'),
      ]);
      if (statsResult.tokens) {
        this.stats = {
          input: statsResult.tokens.inputTokens,
          output: statsResult.tokens.outputTokens,
          cacheRead: statsResult.tokens.cacheRead,
          cacheWrite: statsResult.tokens.cacheWrite,
          cost: typeof statsResult.cost === 'number' ? statsResult.cost : 0,
        };
      }
      const usage = usageResult as {
        tokens?: number;
        contextWindow?: number;
        percent?: number;
      };
      if (typeof usage.percent === 'number' && typeof usage.contextWindow === 'number') {
        this.contextUsage = {
          tokens: usage.tokens ?? 0,
          contextWindow: usage.contextWindow,
          percent: usage.percent,
        };
      }
    } catch {
      // stats 拉取失败静默（footer 不阻塞主流程）
    }
  }

  invalidate(): void {
    this.customComponent?.invalidate?.();
  }

  /** 扩展状态行：key 幂等覆盖，空值清除。 */
  setExtensionStatus(key: string, text: string | undefined): void {
    if (text === undefined || text === '') this.extensionStatus.delete(key);
    else this.extensionStatus.set(key, text);
  }

  /** 扩展状态表（key → 文本）。 */
  private readonly extensionStatus = new Map<string, string>();

  /** 自定义 footer 组件（—整件替换；undefined 恢复默认）。 */
  private customComponent:
    | { render(width: number): string[]; invalidate?(): void; dispose?(): void }
    | undefined;

  /**
   * 整件替换 footer（扩展 ctx.setFooter 的宿主端）。
   * env 回灌宿主算好的数据（git branch/扩展状态/快照/invoke）——自定义
   * footer 不丢宿主信息。旧组件有 dispose 则调用。
   */
  setCustomFooter(factory: ((env: unknown) => unknown) | undefined): void {
    const old = this.customComponent;
    if (old && typeof old.dispose === 'function') old.dispose();
    if (factory === undefined) {
      this.customComponent = undefined;
      return;
    }
    const env = {
      cwd: this.cwd,
      getGitBranch: () => this.branch,
      getExtensionStatuses: () => [...this.extensionStatus.values()],
      getSnapshot: () => this.runtime.store.currentSnapshot,
      invoke: (method: string, params?: Record<string, unknown>) =>
        this.runtime.invoke(method as never, params as never),
    };
    this.customComponent = factory(env) as FooterView['customComponent'];
  }

  render(width: number): string[] {
    // 自定义 footer 整件替换（—异常回退默认渲染，不炸布局）
    if (this.customComponent) {
      try {
        return this.customComponent
          .render(width)
          .map((line) => truncateToWidth(line, width));
      } catch {
        // 落入默认渲染
      }
    }
    const snapshot = this.runtime.store.currentSnapshot;

    // —— 行 1：cwd (branch) • 会话名 ——
    const home = process.env.HOME ?? '';
    const cwdDisplay = home && this.cwd.startsWith(home) ? `~${this.cwd.slice(home.length)}` : this.cwd;
    const parts = [colors.accent(cwdDisplay)];
    if (this.branch) parts.push(colors.muted(`(${this.branch})`));
    const sessionName = snapshot?.sessionName;
    // 会话名是后端数据——与终端标题同一纪律：控制字符净化（防注入序列）
    if (sessionName)
      parts.push(colors.dim(`• ${sessionName.replace(/[\x00-\x1f\x7f]/g, '').trim()}`));
    const line1 = parts.join(' ');

    // —— 行 2：token 统计 · 命中率 · 成本 · 上下文用量 · model · thinking ——
    const left: string[] = [];
    if (this.stats) {
      left.push(colors.dim(`↑${this.stats.input}`));
      left.push(colors.dim(`↓${this.stats.output}`));
      if (this.stats.cacheRead > 0) left.push(colors.dim(`R${this.stats.cacheRead}`));
      if (this.stats.cacheWrite > 0) left.push(colors.dim(`W${this.stats.cacheWrite}`));
      // 命中率（cache_read 占全部输入比）与成本
      if (this.stats.input > 0 && this.stats.cacheRead > 0) {
        const hitRate = Math.min(100, (this.stats.cacheRead / this.stats.input) * 100);
        left.push(colors.dim(`${hitRate.toFixed(0)}%`));
      }
      if (this.stats.cost > 0) {
        left.push(colors.dim(`$${this.stats.cost.toFixed(4)}`));
      }
    }
    // 上下文用量：percent/窗口（>70% 黄 >90% 红；auto-compact 开带 (auto)）
    if (this.contextUsage && this.contextUsage.contextWindow > 0) {
      const percent = Math.round(this.contextUsage.percent);
      const windowText = formatTokenCount(this.contextUsage.contextWindow);
      const compaction = this.settings().compaction as { enabled?: boolean } | undefined;
      const auto = compaction?.enabled === false ? '' : '(auto)';
      const text = `${percent}%/${windowText}${auto}`;
      const colored =
        percent > 90
          ? colors.error(text)
          : percent > 70
            ? colors.warning(text)
            : colors.dim(text);
      left.push(colored);
    }
    const modelRef = snapshot?.model;
    const model = modelRef ? `${modelRef.provider}/${modelRef.id}` : 'no model';
    // 当前角色（+ persona override 标记——/agent /persona 切换的可见落点）
    const agentName = snapshot?.agentName;
    const personaOverride = snapshot?.personaOverride;
    const role = agentName
      ? personaOverride
        ? `${agentName}·${personaOverride}`
        : agentName
      : undefined;
    const thinking = snapshot?.thinkingLevel;
    const modelThinking =
      thinking && thinking !== 'off' ? `${model} · ${thinking}` : model;
    const right = role ? `${role} · ${modelThinking}` : modelThinking;

    const leftText = left.join(' ');
    const gap = Math.max(1, width - visibleLength(leftText) - visibleLength(right) - 1);
    const line2 = leftText ? `${leftText}${' '.repeat(gap)}${colors.dim(right)}` : colors.dim(right);

    // —— 行 3：扩展状态文本（—按 key 排序，单行截断）——
    const statusLines: string[] = [];
    if (this.extensionStatus.size > 0) {
      const texts = [...this.extensionStatus.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([, text]) => text);
      statusLines.push(colors.dim(truncateToWidth(texts.join(' · '), width)));
    }

    return [
      truncateToWidth(line1, width),
      truncateToWidth(line2, width),
      ...statusLines,
      ...this.regionLines(width),
    ];
  }

  /**
   * region:footer 部件行（包经 ui/index.ts 的 registerRegion 贡献）。
   * producer 每帧调用（纯函数便宜），输出指纹不变则复用组件（组件构造贵）；
   * 部件异常静默——不炸 footer 主行。
   */
  private regionLines(width: number): string[] {
    const producer = this.runtime.slots.resolve<RegionContext, NovaBlock[]>(
      regionSlot('footer'),
    );
    if (!producer) {
      this.regionComponents = undefined;
      this.regionFingerprint = undefined;
      return [];
    }
    let blocks: NovaBlock[];
    try {
      blocks = producer({ cwd: this.cwd });
    } catch {
      return [];
    }
    if (!Array.isArray(blocks) || blocks.length === 0) return [];
    const fingerprint = JSON.stringify(blocks);
    if (fingerprint !== this.regionFingerprint) {
      this.regionFingerprint = fingerprint;
      // 行宽防线：footer 区域块产物超宽行不得崩掉整个 TUI
      this.regionComponents = blocksToComponents(blocks, this.runtime.slots).map(
        guardComponentLineWidth,
      );
    }
    return (this.regionComponents ?? []).flatMap((component) => component.render(width));
  }
}

/** 可见宽度（去 ANSI）。 */
function visibleLength(text: string): number {
  return text.replace(/\x1b\[[0-9;]*m/g, '').length;
}

/** token 窗口格式化（128000 → 128k）。 */
function formatTokenCount(count: number): string {
  if (count >= 1000) return `${Math.round(count / 1000)}k`;
  return String(count);
}
