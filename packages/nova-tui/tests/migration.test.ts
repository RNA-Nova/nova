/**
 * 前端域迁移测试（前后端分治 §9——``src/migration.ts``）。
 *
 * 语义钉板：旧位（~/.nova/agent 下的 ui-settings.json/ui-state/keybindings.json/
 * themes，项目级 .nova/keybindings.json）整体搬入 frontend/tui/ 半区；
 * mv 语义、幂等、新位已有内容不合并不覆盖。
 *
 * user 级根经 HOME 环境变量重定向到临时目录（os.homedir 在 POSIX 下优先
 * 读 $HOME）；node:test 每文件独立进程，改 env 不污染其他测试文件。
 */

import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, it } from 'node:test';

import { migrateFrontendLayout } from '../src/migration.js';

let home: string;
let cwd: string;
let savedHome: string | undefined;
let savedUserProfile: string | undefined;

// 每个用例独立 HOME/cwd（迁移有真实文件副作用，用例间不共享）
beforeEach(() => {
  savedHome = process.env.HOME;
  savedUserProfile = process.env.USERPROFILE;
  home = mkdtempSync(join(tmpdir(), 'nova-migration-home-'));
  cwd = mkdtempSync(join(tmpdir(), 'nova-migration-cwd-'));
  // os.homedir 的环境变量语义分平台：POSIX 读 $HOME，Windows 读 %USERPROFILE%
  process.env.HOME = home;
  if (process.platform === 'win32') process.env.USERPROFILE = home;
});

afterEach(() => {
  if (savedHome === undefined) delete process.env.HOME;
  else process.env.HOME = savedHome;
  if (savedUserProfile === undefined) delete process.env.USERPROFILE;
  else process.env.USERPROFILE = savedUserProfile;
  rmSync(home, { recursive: true, force: true });
  rmSync(cwd, { recursive: true, force: true });
});

/** 旧位/新位路径助手（user 级）。 */
const agentDir = () => join(home, '.nova', 'agent');
const tuiDir = () => join(home, '.nova', 'agent', 'frontend', 'tui');

function writeLegacyUserAssets(): void {
  mkdirSync(agentDir(), { recursive: true });
  writeFileSync(join(agentDir(), 'ui-settings.json'), '{"a":1}');
  mkdirSync(join(agentDir(), 'ui-state'), { recursive: true });
  writeFileSync(join(agentDir(), 'ui-state', 'nova-tui.json'), '{"v":1}');
  writeFileSync(join(agentDir(), 'keybindings.json'), '{}');
  mkdirSync(join(agentDir(), 'themes'), { recursive: true });
  writeFileSync(join(agentDir(), 'themes', 'mine.json'), '{"name":"mine"}');
}

describe('migrateFrontendLayout', () => {
  it('user 级四项整体搬入 frontend/tui/ 半区（mv 语义）', () => {
    writeLegacyUserAssets();

    const messages = migrateFrontendLayout(cwd);

    assert.equal(messages.length, 4);
    // 新位内容就位
    assert.equal(readFileSync(join(tuiDir(), 'settings.json'), 'utf-8'), '{"a":1}');
    assert.equal(readFileSync(join(tuiDir(), 'state', 'nova-tui.json'), 'utf-8'), '{"v":1}');
    assert.equal(readFileSync(join(tuiDir(), 'keybindings.json'), 'utf-8'), '{}');
    assert.equal(readFileSync(join(tuiDir(), 'themes', 'mine.json'), 'utf-8'), '{"name":"mine"}');
    // 旧位不留副本
    assert.equal(existsSync(join(agentDir(), 'ui-settings.json')), false);
    assert.equal(existsSync(join(agentDir(), 'ui-state')), false);
    assert.equal(existsSync(join(agentDir(), 'keybindings.json')), false);
    assert.equal(existsSync(join(agentDir(), 'themes')), false);
    // 后端状态根的其他条目不被触碰
    assert.equal(existsSync(agentDir()), true);
  });

  it('project 级 keybindings.json 搬入 .nova/frontend/tui/', () => {
    mkdirSync(join(cwd, '.nova'), { recursive: true });
    writeFileSync(join(cwd, '.nova', 'keybindings.json'), '{"app.exit":"ctrl+q"}');

    const messages = migrateFrontendLayout(cwd);

    assert.equal(messages.length, 1);
    assert.equal(
      readFileSync(join(cwd, '.nova', 'frontend', 'tui', 'keybindings.json'), 'utf-8'),
      '{"app.exit":"ctrl+q"}',
    );
    assert.equal(existsSync(join(cwd, '.nova', 'keybindings.json')), false);
  });

  it('幂等：二次运行零副作用', () => {
    writeLegacyUserAssets();
    migrateFrontendLayout(cwd);

    const second = migrateFrontendLayout(cwd);

    assert.deepEqual(second, []);
    assert.equal(readFileSync(join(tuiDir(), 'settings.json'), 'utf-8'), '{"a":1}');
  });

  it('新位已有内容：不搬不合并不覆盖，返回诊断', () => {
    writeLegacyUserAssets();
    mkdirSync(tuiDir(), { recursive: true });
    writeFileSync(join(tuiDir(), 'settings.json'), '{"b":2}');

    const messages = migrateFrontendLayout(cwd);

    // settings.json 冲突跳过（诊断），其余三项照常搬迁
    assert.equal(messages.length, 4);
    assert.ok(messages.some((m) => m.includes('不合并不覆盖') && m.includes('ui-settings.json')));
    assert.equal(readFileSync(join(tuiDir(), 'settings.json'), 'utf-8'), '{"b":2}'); // 新位未覆盖
    assert.equal(readFileSync(join(agentDir(), 'ui-settings.json'), 'utf-8'), '{"a":1}'); // 旧位保留
    assert.equal(existsSync(join(tuiDir(), 'state', 'nova-tui.json')), true);
  });

  it('无旧位零副作用（不创建 frontend/）', () => {
    const messages = migrateFrontendLayout(cwd);
    assert.deepEqual(messages, []);
    assert.equal(existsSync(join(home, '.nova')), false);
  });
});
