/**
 * /changelog 与 What's New（pi utils/changelog.ts + interactive-mode
 * getChangelogForDisplay 对位的轻量版）。
 *
 * - ``loadChangelog``：读 CHANGELOG.md 原文（dist/assets/ 构建期拷贝——
 *   copy-assets.mjs；tsx 开发态回退包内/仓库根 CHANGELOG.md）；
 * - ``renderChangelogEntry``：最新 Unreleased 段（无则最新版本段）渲为
 *   markdown 文本，供 transcript 以 markdown 块插入（装配归主代理）；
 * - ``getWhatsNewIfNeeded``：ui-state 记 ``lastSeenVersion``，package.json
 *   version 前进时返回提示文案一次并回写（首装不弹——pi 同款语义）。
 */

import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import type { UIStateStore } from 'nova-tui';

/** ui-state 命名空间（nova-tui 自有 KV——不进设置面板）。 */
const WHATS_NEW_NAMESPACE = 'nova-tui';
const LAST_SEEN_VERSION_KEY = 'lastSeenVersion';

/** CHANGELOG.md 候选路径（按优先级——首个存在即取）。 */
function changelogCandidates(): string[] {
  return [
    // 构建产物：dist/modes/tui/utils/ → dist/assets/CHANGELOG.md（copy-assets.mjs）
    fileURLToPath(new URL('../../../assets/CHANGELOG.md', import.meta.url)),
    // 开发态：包内 CHANGELOG.md（packages/nova-harness/frontend/）
    fileURLToPath(new URL('../../../../CHANGELOG.md', import.meta.url)),
    // 开发态：仓库根 CHANGELOG.md（monorepo 单一出处——上六级）
    fileURLToPath(new URL('../../../../../../CHANGELOG.md', import.meta.url)),
  ];
}

/** 读 CHANGELOG.md 原文；不可得返回空串。 */
export function loadChangelog(): string {
  for (const path of changelogCandidates()) {
    try {
      if (existsSync(path)) {
        const content = readFileSync(path, 'utf-8').trim();
        if (content) return content;
      }
    } catch {
      // 读失败尝下一个候选
    }
  }
  return '';
}

export interface ChangelogSection {
  /** 'Unreleased' 或 'x.y.z'。 */
  version: string;
  /** 段内容（含标题行）。 */
  content: string;
}

/** 解析 Keep a Changelog 段落（## [Unreleased] / ## [x.y.z] 标题切分）。 */
export function parseChangelogSections(content: string): ChangelogSection[] {
  const sections: ChangelogSection[] = [];
  let current: { version: string; lines: string[] } | undefined;
  for (const line of content.split('\n')) {
    const header = /^##\s+\[(Unreleased|\d+\.\d+\.\d+)\]/.exec(line);
    if (header) {
      if (current) sections.push({ version: current.version, content: current.lines.join('\n').trim() });
      current = { version: header[1]!, lines: [line] };
    } else if (current) {
      current.lines.push(line);
    }
  }
  if (current) sections.push({ version: current.version, content: current.lines.join('\n').trim() });
  return sections.filter((section) => section.content.length > 0);
}

/**
 * 渲染最新 changelog 段为 markdown 文本（Unreleased 优先，无则最新版本段；
 * 无 changelog 返回空串）。transcript 插入侧（/changelog 命令）归装配。
 */
export function renderChangelogEntry(): string {
  const sections = parseChangelogSections(loadChangelog());
  const unreleased = sections.find((section) => section.version === 'Unreleased');
  return (unreleased ?? sections[0])?.content ?? '';
}

/** 当前包版本（包根 package.json——dist/tsx 两态上四级均为包根）。 */
export function readPackageVersion(): string {
  try {
    const pkg = JSON.parse(
      readFileSync(new URL('../../../../package.json', import.meta.url), 'utf-8'),
    ) as { version?: unknown };
    return typeof pkg.version === 'string' ? pkg.version : '';
  } catch {
    return '';
  }
}

/**
 * What's New 判定：版本前进 → 返回提示文案并回写 lastSeenVersion；
 * 同版本 / 首装（无记录，仅落账）/ 版本读不到 → null。
 */
export function getWhatsNewIfNeeded(uiState: UIStateStore, version: string): string | null {
  if (!version) return null;
  const last = uiState.get<string>(WHATS_NEW_NAMESPACE, LAST_SEEN_VERSION_KEY);
  if (last === version) return null;
  uiState.set(WHATS_NEW_NAMESPACE, LAST_SEEN_VERSION_KEY, version);
  if (typeof last !== 'string' || !last) return null; // 首装不弹（pi 同款）
  return `nova 已更新：v${last} → v${version}（/changelog 查看更新内容）`;
}
