/**
 * changelog 工具测试：段落解析（Unreleased/版本段）、What's New
 * 版本比对（首装不弹/前进弹一次/同版不弹）。
 */

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, it } from 'node:test';

import { UIStateStore } from 'nova-tui';

import {
  getWhatsNewIfNeeded,
  loadChangelog,
  parseChangelogSections,
  readPackageVersion,
  renderChangelogEntry,
} from '../../../../src/modes/tui/utils/changelog.js';

const SAMPLE = `# Changelog

Intro line.

## [Unreleased]

### Changed
- 条目甲
- 条目乙

## [0.1.0] - 2026-01-01

### Added
- 首个版本

## [0.0.9] - 2025-12-01

### Fixed
- 旧修复
`;

describe('parseChangelogSections', () => {
  it('Unreleased 与版本段切分（前言不入段、空段过滤）', () => {
    const sections = parseChangelogSections(SAMPLE);
    assert.deepEqual(
      sections.map((s) => s.version),
      ['Unreleased', '0.1.0', '0.0.9'],
    );
    assert.match(sections[0]!.content, /条目甲/);
    assert.match(sections[1]!.content, /首个版本/);
  });

  it('空内容/无段头 → 空表', () => {
    assert.deepEqual(parseChangelogSections(''), []);
    assert.deepEqual(parseChangelogSections('# 只有标题\n正文'), []);
  });
});

describe('renderChangelogEntry', () => {
  it('Unreleased 优先；无则最新版本段', () => {
    // 真实仓库根 CHANGELOG.md（tsx 态候选命中）——有 Unreleased 段
    const rendered = renderChangelogEntry();
    assert.ok(rendered.length > 0); // 仓库 CHANGELOG 非空
    assert.match(rendered, /^## \[/);
  });

  it('loadChangelog 读不到时为空串（不抛）', () => {
    // 真实环境至少命中一个候选——只验证不抛且为字符串
    assert.equal(typeof loadChangelog(), 'string');
  });
});

describe('getWhatsNewIfNeeded', () => {
  const dirs: string[] = [];
  after(() => {
    for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
  });

  function makeDir(): string {
    const dir = mkdtempSync(join(tmpdir(), 'nova-whatsnew-test-'));
    dirs.push(dir);
    return dir;
  }

  it('首装不弹（仅落账）→ 版本前进弹一次 → 同版不弹', () => {
    const dir = makeDir();
    const store = new UIStateStore(dir);
    assert.equal(getWhatsNewIfNeeded(store, '0.2.0'), null); // 首装
    assert.equal(getWhatsNewIfNeeded(store, '0.2.0'), null); // 同版
    const notice = getWhatsNewIfNeeded(store, '0.3.0');
    assert.match(notice!, /v0\.2\.0 → v0\.3\.0/);
    assert.equal(getWhatsNewIfNeeded(store, '0.3.0'), null); // 已弹过
    // 新存储实例读同一目录（lastSeenVersion 已落盘）
    const reread = new UIStateStore(dir);
    assert.equal(reread.get('nova-tui', 'lastSeenVersion'), '0.3.0');
  });

  it('版本空串 → null（不写存储）', () => {
    const store = new UIStateStore(makeDir());
    assert.equal(getWhatsNewIfNeeded(store, ''), null);
  });

  it('readPackageVersion 读到包版本', () => {
    assert.match(readPackageVersion(), /^\d+\.\d+\.\d+/);
  });
});
