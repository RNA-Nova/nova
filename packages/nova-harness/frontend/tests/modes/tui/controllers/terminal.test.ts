/**
 * TerminalController OSC 发射测试：OSC 0 标题（净化/去重/name 段省略）、
 * OSC 9;4 进度（状态映射/设置门控/清除幂等）、前端设置即时应用。
 */

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, beforeEach, describe, it } from 'node:test';

import { UISettings, type SessionSnapshot } from 'nova-client';

import { NovaBus } from '../../../../src/bus.js';
import { MirrorStore } from '../../../../src/mirror/store.js';
import type { NovaEventEnvelope } from '../../../../src/protocol/nova-wire.gen.js';
import {
  applyFrontendSettings,
  clearTerminalProgress,
  initTerminalIntegration,
  notifyTurnEnded,
  resetTerminalForTest,
  setTerminalWriterForTest,
  updateProgress,
  updateTitle,
} from '../../../../src/modes/tui/controllers/terminal.js';
import { initTuiSettings } from '../../../../src/modes/tui/utils/tui-settings.js';

let written: string[] = [];
let tmpDir: string;

before(() => {
  tmpDir = mkdtempSync(join(tmpdir(), 'nova-terminal-test-'));
  initTuiSettings(new UISettings(join(tmpDir, 'ui-settings.json')));
});

after(() => {
  resetTerminalForTest();
  rmSync(tmpDir, { recursive: true, force: true });
});

beforeEach(() => {
  resetTerminalForTest(); // 清绑定/keepalive/标题缓存
  written = [];
  setTerminalWriterForTest((data) => {
    written.push(data);
    return true;
  });
});

function snapshot(partial: Partial<SessionSnapshot>): SessionSnapshot {
  return partial as SessionSnapshot;
}

describe('updateTitle（OSC 0）', () => {
  it('有会话名：nova - <name> - <cwd基名>', () => {
    updateTitle(snapshot({ sessionName: '调试会话', cwd: '/Users/x/proj/nova' }));
    assert.deepEqual(written, ['\x1b]0;nova - 调试会话 - nova\x07']);
  });

  it('无会话名：省掉 name 段', () => {
    updateTitle(snapshot({ sessionName: null, cwd: '/Users/x/proj/nova' }));
    assert.deepEqual(written, ['\x1b]0;nova - nova\x07']);
  });

  it('快照为空：回退 process.cwd() 基名', () => {
    updateTitle(null);
    const base = process.cwd().split('/').pop()!;
    assert.deepEqual(written, [`\x1b]0;nova - ${base}\x07`]);
  });

  it('控制字符净化（会话名含 ESC/换行不注入序列）', () => {
    updateTitle(snapshot({ sessionName: 'a\x1b]0;evil\x07\nb', cwd: '/x/y' }));
    // ESC/BEL/换行被剥除——残留 ']0;evil' 无前导 ESC，不构成序列
    assert.deepEqual(written, ['\x1b]0;nova - a]0;evilb - y\x07']);
  });

  it('标题未变不重写（快照订阅每次都调——去重）', () => {
    const snap = snapshot({ sessionName: 's', cwd: '/x/y' });
    updateTitle(snap);
    updateTitle(snap);
    assert.equal(written.length, 1);
  });
});

describe('updateProgress（OSC 9;4）', () => {
  it('terminal_progress 关闭（默认）：只清不置', () => {
    updateProgress('working');
    assert.deepEqual(written, []); // 关闭时不发射
  });

  it('开启后：working/compacting 置位 + 其余状态清除', () => {
    const uiSettings = new UISettings(join(tmpDir, 'ui-settings.json'));
    initTuiSettings(uiSettings);
    uiSettings.set('terminal_progress', true);

    updateProgress('working');
    assert.deepEqual(written, ['\x1b]9;4;3\x07']);
    updateProgress('compacting'); // 已置位不重复（keepalive 接管）
    assert.equal(written.length, 1);
    updateProgress('idle');
    assert.deepEqual(written, ['\x1b]9;4;3\x07', '\x1b]9;4;0;\x07']);
    updateProgress('idle'); // 清除幂等——不重复发
    assert.equal(written.length, 2);

    uiSettings.set('terminal_progress', false);
    updateProgress('working');
    assert.equal(written.length, 2); // 关闭后不再置位
  });
});

describe('notifyTurnEnded（桌面通知 OSC 9/777/99）', () => {
  it('三条序列并发写出，title/body 各归其位', () => {
    notifyTurnEnded('nova', '调试会话');
    assert.deepEqual(written, [
      '\x1b]9;调试会话\x07',
      '\x1b]777;notify;nova;调试会话\x07',
      '\x1b]99;i=1:d=0;nova\x1b\\',
    ]);
  });

  it('控制字符剥离（title/body 含 BEL/ESC/换行不注入序列）', () => {
    notifyTurnEnded('no\x07va', 'a\x1b]9;evil\x07\nb');
    // ESC/BEL/换行被剥除——残留 ']9;evil' 无前导 ESC，不构成序列
    assert.deepEqual(written, [
      '\x1b]9;a]9;evilb\x07',
      '\x1b]777;notify;nova;a]9;evilb\x07',
      '\x1b]99;i=1:d=0;nova\x1b\\',
    ]);
  });

  it('desktop_notify 关闭时不写', () => {
    const uiSettings = new UISettings(join(tmpDir, 'ui-settings.json'));
    initTuiSettings(uiSettings);
    uiSettings.set('desktop_notify', false);
    notifyTurnEnded('nova', '回复完成');
    assert.deepEqual(written, []);
    uiSettings.set('desktop_notify', true); // 还原——后续用例不受污染
  });

  it('onDerived 接线：agent_end 事件序列 → notify 恰好一次', () => {
    const ev = (type: string, data: unknown): NovaEventEnvelope =>
      ({ type, data }) as unknown as NovaEventEnvelope;
    const bus = new NovaBus();
    const store = new MirrorStore(bus); // mirror 特权订阅：agent_end → turn:ended
    bus.onDerived('turn:ended', () => {
      notifyTurnEnded('nova', store.currentSnapshot?.sessionName ?? '回复完成');
    });
    bus.publish(ev('agent_start', {}));
    bus.publish(ev('message_start', { message: { role: 'user', content: 'hi' } }));
    assert.equal(written.length, 0); // turn 进行中不通知
    bus.publish(ev('agent_end', { messages: [] }));
    assert.equal(written.length, 3); // 恰好一次（三序列一组）
    assert.equal(written[0], '\x1b]9;回复完成\x07'); // 无快照会话名 → 回退文案
  });
});

describe('applyFrontendSettings', () => {
  it('未绑定 tui/editor 时 no-op（设置面板动作不炸）', () => {
    applyFrontendSettings();
  });

  it('绑定后按持久化设置应用（setClearOnShrink/setPaddingX/setAutocompleteMaxVisible）', () => {
    const uiSettings = new UISettings(join(tmpDir, 'ui-settings.json'));
    initTuiSettings(uiSettings);
    uiSettings.set('clear_on_shrink', false);
    uiSettings.set('editor_padding', 2);
    uiSettings.set('autocomplete_max_items', 15);

    const calls = {
      clearOnShrink: [] as boolean[],
      padding: [] as number[],
      maxVisible: [] as number[],
    };
    const tui = { setClearOnShrink: (v: boolean) => void calls.clearOnShrink.push(v) };
    const editor = {
      setPaddingX: (v: number) => void calls.padding.push(v),
      setAutocompleteMaxVisible: (v: number) => void calls.maxVisible.push(v),
    };
    initTerminalIntegration({
      tui: tui as never,
      editorRef: { current: editor as never },
    });
    assert.deepEqual(calls.clearOnShrink, [false]);
    assert.deepEqual(calls.padding, [2]);
    assert.deepEqual(calls.maxVisible, [15]);

    // 面板变更后再应用（setter 可选——自定义编辑器缺方法不炸）
    uiSettings.set('editor_padding', 0);
    initTerminalIntegration({ editorRef: { current: {} as never } });
    applyFrontendSettings();
  });
});

describe('setTitleOverride（扩展标题原语）', () => {
  it('覆盖生效：自动标题停写，覆盖文本净化后写入', async () => {
    const { setTitleOverride } = await import(
      '../../../../src/modes/tui/controllers/terminal.js'
    );
    setTitleOverride('我的标题\x1b]0;evil\x07');
    assert.deepEqual(written, ['\x1b]0;我的标题]0;evil\x07']);
    // 覆盖在位：快照驱动的自动标题不再写
    written = [];
    updateTitle(snapshot({ sessionName: 'x', cwd: '/a/b' }));
    assert.deepEqual(written, []);
  });

  it('清除后恢复自动标题（下一次 updateTitle 无条件重写）', async () => {
    const { setTitleOverride } = await import(
      '../../../../src/modes/tui/controllers/terminal.js'
    );
    setTitleOverride('覆盖');
    written = [];
    setTitleOverride(undefined);
    updateTitle(snapshot({ sessionName: null, cwd: '/a/b' }));
    assert.deepEqual(written, ['\x1b]0;nova - b\x07']);
  });
});
