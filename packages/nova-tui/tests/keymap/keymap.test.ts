/**
 * keymap/ 键位系统测试：loader 单文件加载与合并、manager 三级合并与冲突。
 *
 * 全部走临时目录 fixture，不碰真实 ~/.nova/agent（路径注入）。
 */

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, describe, it } from 'node:test';

import { TUI_KEYBINDINGS } from '@earendil-works/pi-tui';

import {
  loadKeybindingsFile,
  mergeKeybindingsConfigs,
} from '../../src/keymap/loader.js';
import { NovaKeybindingsManager } from '../../src/keymap/manager.js';

/** 机械层测试夹具表（自含——不依赖 modes/tui 的宿主表，方向干净）。 */
const TEST_DEFAULTS = {
  ...TUI_KEYBINDINGS,
  'app.interrupt': { defaultKeys: 'escape', description: 'test' },
  'app.exit': { defaultKeys: 'ctrl+d', description: 'test' },
  'app.tools.expand': { defaultKeys: 'ctrl+o', description: 'test' },
  'app.clipboard.paste': { defaultKeys: 'ctrl+v', description: 'test' },
} as const;
const TEST_RESERVED = ['app.interrupt'] as const;

let dir: string;

before(() => {
  dir = mkdtempSync(join(tmpdir(), 'nova-keymap-test-'));
});
after(() => {
  rmSync(dir, { recursive: true, force: true });
});

function writeJson(name: string, content: unknown): string {
  const path = join(dir, name);
  writeFileSync(path, typeof content === 'string' ? content : JSON.stringify(content));
  return path;
}

describe('loadKeybindingsFile', () => {
  it('文件不存在 → 空配置空诊断', () => {
    const result = loadKeybindingsFile(join(dir, 'nonexistent.json'), TEST_DEFAULTS);
    assert.deepEqual(result.config, {});
    assert.deepEqual(result.diagnostics, []);
  });

  it('合法配置：字符串与数组值均解析', () => {
    const path = writeJson('valid.json', {
      'app.interrupt': 'f12',
      'app.tools.expand': ['ctrl+o', 'f9'],
    });
    const result = loadKeybindingsFile(path, TEST_DEFAULTS);
    assert.equal(result.config['app.interrupt'], 'f12');
    assert.deepEqual(result.config['app.tools.expand'], ['ctrl+o', 'f9']);
    assert.deepEqual(result.diagnostics, []);
  });

  it('空数组 = 禁用该动作', () => {
    const path = writeJson('disable.json', { 'app.clipboard.paste': [] });
    const result = loadKeybindingsFile(path, TEST_DEFAULTS);
    assert.deepEqual(result.config['app.clipboard.paste'], []);
    assert.deepEqual(result.diagnostics, []);
  });

  it('坏 JSON → 诊断，不抛', () => {
    const path = writeJson('broken.json', '{ not json');
    const result = loadKeybindingsFile(path, TEST_DEFAULTS);
    assert.deepEqual(result.config, {});
    assert.equal(result.diagnostics.length, 1);
    assert.match(result.diagnostics[0]!, /JSON 解析失败/);
  });

  it('顶层非对象 → 诊断', () => {
    const path = writeJson('array.json', ['ctrl+x']);
    const result = loadKeybindingsFile(path, TEST_DEFAULTS);
    assert.deepEqual(result.config, {});
    assert.match(result.diagnostics[0]!, /顶层必须是对象/);
  });

  it('未知 actionId → 诊断并跳过，合法项保留', () => {
    const path = writeJson('unknown.json', {
      'app.nonexistent': 'ctrl+z',
      'app.exit': 'ctrl+q',
    });
    const result = loadKeybindingsFile(path, TEST_DEFAULTS);
    assert.equal(result.config['app.nonexistent'], undefined);
    assert.equal(result.config['app.exit'], 'ctrl+q');
    assert.equal(result.diagnostics.length, 1);
    assert.match(result.diagnostics[0]!, /未知键位动作/);
  });

  it('非法值类型 → 诊断并跳过', () => {
    const path = writeJson('badvalue.json', {
      'app.interrupt': 42,
      'app.clear': { key: 'ctrl+c' },
      'app.exit': 'ctrl+q',
    });
    const result = loadKeybindingsFile(path, TEST_DEFAULTS);
    assert.deepEqual(Object.keys(result.config), ['app.exit']);
    assert.equal(result.diagnostics.length, 2);
  });
});

describe('mergeKeybindingsConfigs', () => {
  it('后者覆盖前者同名键位，不同键位并存', () => {
    const merged = mergeKeybindingsConfigs(
      { 'app.interrupt': 'escape', 'app.exit': 'ctrl+d' },
      { 'app.interrupt': 'f12' },
    );
    assert.equal(merged['app.interrupt'], 'f12');
    assert.equal(merged['app.exit'], 'ctrl+d');
  });
});

describe('NovaKeybindingsManager', () => {
  it('三级合并：project 覆盖 user 同名，user 独有保留，未配置回退 builtin', () => {
    const user = writeJson('user.json', {
      'app.interrupt': 'f11',
      'app.exit': 'ctrl+q',
    });
    const project = writeJson('project.json', { 'app.interrupt': 'f12' });
    const { manager, diagnostics } = NovaKeybindingsManager.create(dir, {
      defaults: TEST_DEFAULTS,
      reserved: TEST_RESERVED,
      paths: {
        user,
        project,
      },
    });
    assert.deepEqual(diagnostics, []);
    assert.deepEqual(manager.getKeys('app.interrupt'), ['f12']); // project 赢
    assert.deepEqual(manager.getKeys('app.exit'), ['ctrl+q']); // user 独有
    assert.deepEqual(manager.getKeys('app.tools.expand'), ['ctrl+o']); // builtin 默认
    assert.deepEqual(manager.getKeys('tui.editor.undo'), ['ctrl+-']); // pi-tui 内建默认
  });

  it('matches：重绑定后新键生效、默认键失效', () => {
    const user = writeJson('rebind.json', { 'app.tools.expand': 'ctrl+u' });
    const { manager } = NovaKeybindingsManager.create(dir, {
      defaults: TEST_DEFAULTS,
      reserved: TEST_RESERVED,
      paths: {
        user,
        project: join(dir, 'none.json'),
      },
    });
    // \x15 = ctrl+u：新键生效
    assert.equal(manager.matches('\x15', 'app.tools.expand'), true);
    // \x0f = ctrl+o：默认键不再匹配（整体替换语义）
    assert.equal(manager.matches('\x0f', 'app.tools.expand'), false);
  });

  it('冲突诊断：两个动作绑同一键经 getConflicts 透出', () => {
    const user = writeJson('conflict.json', {
      'app.tools.expand': 'ctrl+x',
      'app.clipboard.paste': 'ctrl+x',
    });
    const { manager } = NovaKeybindingsManager.create(dir, {
      defaults: TEST_DEFAULTS,
      reserved: TEST_RESERVED,
      paths: {
        user,
        project: join(dir, 'none.json'),
      },
    });
    const conflicts = manager.getConflicts();
    assert.equal(conflicts.length, 1);
    assert.equal(conflicts[0]!.key, 'ctrl+x');
    assert.deepEqual(
      [...conflicts[0]!.keybindings].sort(),
      ['app.clipboard.paste', 'app.tools.expand'],
    );
  });

  it('reload：文件改动后重读生效', () => {
    const user = writeJson('reload.json', { 'app.exit': 'ctrl+q' });
    const { manager } = NovaKeybindingsManager.create(dir, {
      defaults: TEST_DEFAULTS,
      reserved: TEST_RESERVED,
      paths: {
        user,
        project: join(dir, 'none.json'),
      },
    });
    assert.deepEqual(manager.getKeys('app.exit'), ['ctrl+q']);
    writeFileSync(user, JSON.stringify({ 'app.exit': 'ctrl+w' }));
    const diagnostics = manager.reload();
    assert.deepEqual(diagnostics, []);
    assert.deepEqual(manager.getKeys('app.exit'), ['ctrl+w']);
  });

  it('isReserved：保留键位查询（M4 扩展快捷键判据）', () => {
    const { manager } = NovaKeybindingsManager.create(dir, {
      defaults: TEST_DEFAULTS,
      reserved: TEST_RESERVED,
      paths: {
        user: join(dir, 'none.json'),
        project: join(dir, 'none.json'),
      },
    });
    assert.equal(manager.isReserved('app.interrupt'), true);
    assert.equal(manager.isReserved('app.tools.expand'), false);
  });
});
