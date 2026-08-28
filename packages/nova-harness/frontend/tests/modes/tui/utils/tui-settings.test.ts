/**
 * tui-settings 前端设置键测试：注册/默认值/读写往返/越界回退。
 */

import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, describe, it } from 'node:test';

import { UISettings } from 'nova-client';

import {
  getAutocompleteMaxItems,
  getEditorPadding,
  getTreeFilterMode,
  initTuiSettings,
  isBranchSummarySkipPrompt,
  isClearOnShrink,
  isDesktopNotifyEnabled,
  isTerminalProgressEnabled,
  setTuiSetting,
} from '../../../../src/modes/tui/utils/tui-settings.js';

let dir: string;
let filePath: string;
let uiSettings: UISettings;

before(() => {
  dir = mkdtempSync(join(tmpdir(), 'nova-tui-settings-test-'));
  filePath = join(dir, 'ui-settings.json');
  uiSettings = new UISettings(filePath);
  initTuiSettings(uiSettings);
});

after(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('tui-settings 前端设置键', () => {
  it('默认值（未显式设置时 getter 回退默认）', () => {
    assert.equal(getTreeFilterMode(), 'default');
    assert.equal(isBranchSummarySkipPrompt(), false);
    assert.equal(getEditorPadding(), 1);
    assert.equal(getAutocompleteMaxItems(), 5);
    assert.equal(isClearOnShrink(), true);
    assert.equal(isTerminalProgressEnabled(), false);
    assert.equal(isDesktopNotifyEnabled(), true);
  });

  it('读写往返 + 落盘（ui-settings.json 真实写入）', () => {
    assert.equal(setTuiSetting('tree_filter_mode', 'labeled-only'), true);
    assert.equal(getTreeFilterMode(), 'labeled-only');
    assert.equal(setTuiSetting('branch_summary_skip_prompt', true), true);
    assert.equal(isBranchSummarySkipPrompt(), true);
    const onDisk = JSON.parse(readFileSync(filePath, 'utf-8')) as Record<string, unknown>;
    assert.equal(onDisk.tree_filter_mode, 'labeled-only');
    assert.equal(onDisk.branch_summary_skip_prompt, true);
  });

  it('类型校验：类型不符拒绝（UISettings.set 语义）', () => {
    assert.equal(setTuiSetting('editor_padding', '2'), false); // string ≠ number
    assert.equal(setTuiSetting('terminal_progress', 'true'), false);
    assert.equal(setTuiSetting('undeclared_key', 1), false); // 未声明键拒绝
  });

  it('非法现值回退默认（越界 clamp / 未知枚举）', () => {
    setTuiSetting('editor_padding', 99);
    assert.equal(getEditorPadding(), 3); // clamp 上限
    setTuiSetting('editor_padding', -5);
    assert.equal(getEditorPadding(), 0); // clamp 下限
    setTuiSetting('autocomplete_max_items', 10);
    assert.equal(getAutocompleteMaxItems(), 10);
    setTuiSetting('tree_filter_mode', 'bogus');
    assert.equal(getTreeFilterMode(), 'default'); // 未知模式回退
    setTuiSetting('tree_filter_mode', 'all');
    assert.equal(getTreeFilterMode(), 'all');
  });

  it('重复 init 幂等（设置面板构造兜底重入不炸）', () => {
    initTuiSettings(uiSettings);
    assert.equal(getTreeFilterMode(), 'all'); // 现值不丢
  });
});
