/**
 * settings/state 子系统测试：UISettings（define/get/set/冲突/持久化）
 * 与 UIStateStore（命名空间隔离 KV）。
 */

import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, describe, it } from 'node:test';

import { SlotRegistry, createExtensionUIAPI } from 'nova-client';

import { UISettings, UIStateStore } from '../../src/settings/store.js';

let dir: string;
before(() => {
  dir = mkdtempSync(join(tmpdir(), 'nova-settings-test-'));
});
after(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('UISettings', () => {
  it('define/get/set 全链：默认值并入 + 类型校验 + 持久化回读', () => {
    const file = join(dir, 'ui-settings.json');
    const settings = new UISettings(file);
    assert.equal(settings.define('myext.interval', { type: 'number', default: 30 }, 'myext'), true);
    assert.equal(settings.get('myext.interval'), 30); // 默认值并入

    assert.equal(settings.set('myext.interval', 60), true);
    assert.equal(settings.get('myext.interval'), 60);
    // 类型不符拒绝
    assert.equal(settings.set('myext.interval', 'not-number'), false);
    // 未声明键拒绝
    assert.equal(settings.set('undeclared.key', 1), false);
    assert.equal(settings.get('undeclared.key'), undefined);

    // 持久化回读（新实例同文件）
    const reread = new UISettings(file);
    assert.equal(reread.get('myext.interval'), 60);
  });

  it('异 owner 冲突拒绝（同 owner 幂等重载）', () => {
    const settings = new UISettings(join(dir, 'conflict.json'));
    assert.equal(settings.define('k', { type: 'string', default: '' }, 'pkg-a'), true);
    assert.equal(settings.define('k', { type: 'string', default: 'x' }, 'pkg-a'), true); // 同 owner 幂等
    assert.equal(settings.define('k', { type: 'number', default: 0 }, 'pkg-b'), false); // 异 owner 冲突
  });

  it('onChange 变更发布', () => {
    const settings = new UISettings(join(dir, 'change.json'));
    const events: string[] = [];
    settings.onChange((key, value) => events.push(`${key}=${value}`));
    settings.define('flag', { type: 'boolean', default: false }, 'pkg');
    settings.set('flag', true);
    assert.deepEqual(events, ['flag=true']);
  });

  it('坏 JSON 文件按空配置启动（不炸）', () => {
    const file = join(dir, 'broken.json');
    writeFileSync(file, '{ not json');
    const settings = new UISettings(file);
    assert.equal(settings.get('any'), undefined);
  });
});

describe('UIStateStore', () => {
  it('命名空间隔离 KV + 持久化回读', () => {
    const store = new UIStateStore(join(dir, 'ui-state'));
    store.set('todo-ext', 'items', ['a', 'b']);
    store.set('bookmark-ext', 'marks', ['x']);
    assert.deepEqual(store.get('todo-ext', 'items'), ['a', 'b']);
    assert.deepEqual(store.all('bookmark-ext'), { marks: ['x'] });
    assert.equal(store.get('todo-ext', 'marks'), undefined); // 隔离

    const reread = new UIStateStore(join(dir, 'ui-state'));
    assert.deepEqual(reread.get('todo-ext', 'items'), ['a', 'b']);
  });

  it('命名空间路径净化（防逃逸）', () => {
    const store = new UIStateStore(join(dir, 'ui-state-safe'));
    store.set('../../etc/evil', 'k', 'v');
    const file = join(dir, 'ui-state-safe', '.._.._etc_evil.json');
    assert.equal(existsSync(file), true); // 净化为安全文件名，未逃逸
  });
});

describe('ExtensionUIAPI · settings/state 通道', () => {
  it('api.settings.define 冲突经碰撞诊断透出；api.state 按包名隔离', () => {
    const settingsFile = join(dir, 'api-settings.json');
    const stateDir = join(dir, 'api-state');
    const slots = new SlotRegistry();
    const collisions: string[] = [];
    const deps = {
      slots,
      onCollision: (key: string) => void collisions.push(key),
      uiSettings: new UISettings(settingsFile),
      uiState: new UIStateStore(stateDir),
    };
    const apiA = createExtensionUIAPI({ ...deps, source: 'pkg-a' });
    const apiB = createExtensionUIAPI({ ...deps, source: 'pkg-b' });

    assert.equal(apiA.settings.define('interval', { type: 'number', default: 30 }), true);
    assert.equal(apiB.settings.define('interval', { type: 'number', default: 5 }), false);
    assert.deepEqual(collisions, ['setting:interval']);

    // 同 owner 重载后读写正常
    apiA.settings.set('interval', 99);
    assert.equal(apiA.settings.get('interval'), 99);
    assert.equal(apiB.settings.get('interval'), 99); // 设置是全局值（键级共享）

    // state 按包名隔离
    apiA.state.set('notes', ['a-note']);
    apiB.state.set('notes', ['b-note']);
    assert.deepEqual(apiA.state.get('notes'), ['a-note']);
    assert.deepEqual(apiB.state.get('notes'), ['b-note']);
  });
});
