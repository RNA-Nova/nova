/**
 * SettingsController 测试：配置项表与 apply 链路
 * （currentSettings 原地更新 / 布尔解析 / 持久化三通道分流 / 即时动作分发）。
 */

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, it } from 'node:test';

import { UISettings, type NovaUIRuntime, type SessionSnapshot } from 'nova-tui';

import { SettingsController } from '../../../../src/modes/tui/controllers/settings.js';
import { getCurrentThemeName, setTheme } from '../../../../src/modes/tui/themes/index.js';
import { getTreeFilterMode, isTerminalProgressEnabled } from '../../../../src/modes/tui/utils/tui-settings.js';

function makeController(options: { snapshot?: Partial<SessionSnapshot>; uiSettings?: UISettings } = {}) {
  const calls = {
    invoke: [] as Array<{ method: string; params: unknown }>,
    info: [] as string[],
    themeApplied: [] as string[],
  };
  const runtime = {
    invoke: async (method: string, params: unknown) => {
      calls.invoke.push({ method, params });
      return { ok: true };
    },
    store: { currentSnapshot: options.snapshot ?? null },
    uiSettings: options.uiSettings,
  } as unknown as NovaUIRuntime;
  const dialogs = { isActive: false, hasLocalDialog: false } as never;
  const transcript = {
    addInfo: (m: string) => void calls.info.push(m),
    addError: () => {},
  } as never;
  const theme = {
    applyTheme: (name: string) => void calls.themeApplied.push(name),
  } as never;
  const currentSettings: Record<string, unknown> = {};
  const controller = new SettingsController(
    runtime,
    dialogs,
    transcript,
    theme,
    currentSettings,
  );
  return { controller, calls, currentSettings };
}

/** 触达私有 apply（选择器交互的落点——组件层输入模拟不在此测）。 */
function applyOf(controller: SettingsController) {
  return (
    controller as unknown as {
      settingItems: () => Array<{ id: string }>;
      apply: (item: { id: string }, value: string) => void;
    }
  );
}

/** 临时 UISettings（前端项持久化的真实存储——独立文件防污染）。 */
function makeUiSettings(): { uiSettings: UISettings; cleanup: () => void } {
  const dir = mkdtempSync(join(tmpdir(), 'nova-ui-settings-test-'));
  return {
    uiSettings: new UISettings(join(dir, 'ui-settings.json')),
    cleanup: () => rmSync(dir, { recursive: true, force: true }),
  };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

describe('SettingsController', () => {
  it('配置项表：当前值从 currentSettings 派生（缺省回退默认）', () => {
    const { controller, currentSettings } = makeController();
    const items = applyOf(controller).settingItems();
    const ids = items.map((item) => item.id);
    assert.deepEqual(ids, [
      'theme',
      'doubleEscapeAction',
      'quietStartup',
      'hideThinkingBlock',
      'showCacheMissNotices',
      'steering_mode',
      'followup_mode',
      'auto_compaction',
      'thinking_level',
      'defaultProjectTrust',
      'roleBoundary',
      'tree_filter_mode',
      'branch_summary_skip_prompt',
      'editor_padding',
      'autocomplete_max_items',
      'clear_on_shrink',
      'terminal_progress',
      'desktop_notify',
    ]);
    const doubleEsc = items.find((item) => item.id === 'doubleEscapeAction');
    assert.equal(doubleEsc?.currentValue?.() ?? (doubleEsc as never), 'tree'); // 默认
    currentSettings.doubleEscapeAction = 'fork';
    const items2 = applyOf(controller).settingItems();
    assert.equal(
      items2.find((item) => item.id === 'doubleEscapeAction')?.currentValue(),
      'fork',
    );
  });

  it('apply：布尔解析 + currentSettings 原地更新 + updateSettings 持久化', async () => {
    const { controller, calls, currentSettings } = makeController();
    const items = applyOf(controller).settingItems();
    const quiet = items.find((item) => item.id === 'quietStartup')!;
    applyOf(controller).apply(quiet as { id: string }, 'true');
    assert.equal(currentSettings.quietStartup, true); // boolean 而非字符串
    await flush();
    const update = calls.invoke.find((call) => call.method === 'updateSettings');
    assert.ok(update);
    assert.deepEqual(update.params, { settings: { quietStartup: true } });
    assert.match(calls.info[0]!, /下次启动生效/); // note 透出
  });

  it('apply：doubleEscapeAction 字符串原样 + 即时生效（getter 现取）', async () => {
    const { controller, calls, currentSettings } = makeController();
    const items = applyOf(controller).settingItems();
    const doubleEsc = items.find((item) => item.id === 'doubleEscapeAction')!;
    applyOf(controller).apply(doubleEsc as { id: string }, 'none');
    assert.equal(currentSettings.doubleEscapeAction, 'none');
    await flush();
    const update = calls.invoke.find((call) => call.method === 'updateSettings');
    assert.deepEqual(update?.params, { settings: { doubleEscapeAction: 'none' } });
  });

  it('apply：theme 项走 ThemeController.applyTheme（skipPersist 不重复写）', async () => {
    const { controller, calls } = makeController();
    const items = applyOf(controller).settingItems();
    const themeItem = items.find((item) => item.id === 'theme')!;
    applyOf(controller).apply(themeItem as { id: string }, 'light');
    assert.deepEqual(calls.themeApplied, ['light']); // 分发到主题控制器
    await flush();
    assert.equal(calls.invoke.length, 0); // skipPersist：applyTheme 内自持久化
  });

  it('theme 项：选项首项为 automatic + 当前值读取真实主题单例', () => {
    const { controller } = makeController();
    setTheme('dark');
    const items = applyOf(controller).settingItems();
    const themeItem = items.find((item) => item.id === 'theme')! as unknown as {
      currentValue: () => string;
      options: () => Array<{ value: string }>;
    };
    assert.equal(themeItem.currentValue(), getCurrentThemeName());
    assert.equal(themeItem.options()[0]?.value, 'automatic'); // 自动档排第一
  });

  it('defaultProjectTrust：后端 updateSettings 持久化 + 生效说明', async () => {
    const { controller, calls, currentSettings } = makeController();
    const items = applyOf(controller).settingItems();
    const trust = items.find((item) => item.id === 'defaultProjectTrust')!;
    applyOf(controller).apply(trust as { id: string }, 'always');
    assert.equal(currentSettings.defaultProjectTrust, 'always');
    await flush();
    const update = calls.invoke.find((call) => call.method === 'updateSettings');
    assert.deepEqual(update?.params, { settings: { defaultProjectTrust: 'always' } });
    assert.match(calls.info[0]!, /新会话生效/);
  });

  it('steering_mode：当前值读快照 + setSteeringMode 持久化（不写缓存/settings）', async () => {
    const { controller, calls, currentSettings } = makeController({
      snapshot: { steeringMode: 'one-at-a-time' } as Partial<SessionSnapshot>,
    });
    const items = applyOf(controller).settingItems();
    const steering = items.find((item) => item.id === 'steering_mode')! as unknown as {
      currentValue: () => string;
    };
    assert.equal(steering.currentValue(), 'one-at-a-time'); // 快照现值
    applyOf(controller).apply(steering as { id: string }, 'all');
    await flush();
    assert.equal(currentSettings.steering_mode, undefined); // skipCache
    const rpc = calls.invoke.find((call) => call.method === 'setSteeringMode');
    assert.deepEqual(rpc?.params, { mode: 'all' });
    assert.equal(calls.invoke.some((call) => call.method === 'updateSettings'), false);
  });

  it('followup_mode：当前值读快照 + setFollowUpMode 持久化', async () => {
    const { controller, calls } = makeController({
      snapshot: { followUpMode: 'all' } as Partial<SessionSnapshot>,
    });
    const items = applyOf(controller).settingItems();
    const followup = items.find((item) => item.id === 'followup_mode')! as unknown as {
      currentValue: () => string;
    };
    assert.equal(followup.currentValue(), 'all');
    applyOf(controller).apply(followup as { id: string }, 'one-at-a-time');
    await flush();
    const rpc = calls.invoke.find((call) => call.method === 'setFollowUpMode');
    assert.deepEqual(rpc?.params, { mode: 'one-at-a-time' });
  });

  it('auto_compaction：布尔解析 + setAutoCompactionEnabled {enabled} 持久化', async () => {
    const { controller, calls } = makeController({
      snapshot: { autoCompactionEnabled: true } as Partial<SessionSnapshot>,
    });
    const items = applyOf(controller).settingItems();
    const autoCompaction = items.find((item) => item.id === 'auto_compaction')! as unknown as {
      currentValue: () => string;
    };
    assert.equal(autoCompaction.currentValue(), 'true');
    applyOf(controller).apply(autoCompaction as { id: string }, 'false');
    await flush();
    const rpc = calls.invoke.find((call) => call.method === 'setAutoCompactionEnabled');
    assert.deepEqual(rpc?.params, { enabled: false });
  });

  it('thinking_level：快照级别表驱动选项；空表（不支持）无候选', () => {
    const unsupported = makeController({ snapshot: { availableThinkingLevels: [] } as never });
    const items = applyOf(unsupported.controller).settingItems();
    const thinking = items.find((item) => item.id === 'thinking_level')! as unknown as {
      options: () => unknown[];
      emptyNote?: string;
    };
    assert.equal(thinking.options().length, 0);
    assert.match(thinking.emptyNote ?? '', /不支持/);

    const supported = makeController({
      snapshot: { availableThinkingLevels: ['off', 'low', 'high'], thinkingLevel: 'low' } as never,
    });
    const items2 = applyOf(supported.controller).settingItems();
    const thinking2 = items2.find((item) => item.id === 'thinking_level')! as unknown as {
      currentValue: () => string;
      options: () => Array<{ value: string }>;
    };
    assert.deepEqual(thinking2.options().map((o) => o.value), ['off', 'low', 'high']);
    assert.equal(thinking2.currentValue(), 'low');
  });

  it('thinking_level apply：setThinkingLevel 持久化', async () => {
    const { controller, calls } = makeController({
      snapshot: { availableThinkingLevels: ['off', 'low'], thinkingLevel: 'off' } as never,
    });
    const items = applyOf(controller).settingItems();
    const thinking = items.find((item) => item.id === 'thinking_level')!;
    applyOf(controller).apply(thinking as { id: string }, 'low');
    await flush();
    const rpc = calls.invoke.find((call) => call.method === 'setThinkingLevel');
    assert.deepEqual(rpc?.params, { level: 'low' });
  });

  it('tree_filter_mode：写 ui-settings（updateSettings 不动）+ getter 现取', async () => {
    const { uiSettings, cleanup } = makeUiSettings();
    try {
      const { controller, calls, currentSettings } = makeController({ uiSettings });
      const items = applyOf(controller).settingItems();
      const treeFilter = items.find((item) => item.id === 'tree_filter_mode')! as unknown as {
        currentValue: () => string;
      };
      assert.equal(treeFilter.currentValue(), 'default');
      applyOf(controller).apply(treeFilter as { id: string }, 'no-tools');
      await flush();
      assert.equal(getTreeFilterMode(), 'no-tools'); // 存储生效
      assert.equal(uiSettings.get('tree_filter_mode'), 'no-tools'); // 落盘对象现取
      assert.equal(currentSettings.tree_filter_mode, undefined); // skipCache
      assert.equal(calls.invoke.length, 0); // 前端项不走 RPC
    } finally {
      cleanup();
    }
  });

  it('terminal_progress：布尔写 ui-settings；editor_padding 数字解析', async () => {
    const { uiSettings, cleanup } = makeUiSettings();
    try {
      const { controller } = makeController({ uiSettings });
      const items = applyOf(controller).settingItems();
      const progress = items.find((item) => item.id === 'terminal_progress')!;
      applyOf(controller).apply(progress as { id: string }, 'true');
      assert.equal(isTerminalProgressEnabled(), true);
      assert.equal(uiSettings.get('terminal_progress'), true);

      const padding = items.find((item) => item.id === 'editor_padding')!;
      applyOf(controller).apply(padding as { id: string }, '3');
      assert.equal(uiSettings.get('editor_padding'), 3); // number 而非字符串
    } finally {
      cleanup();
    }
  });
});
