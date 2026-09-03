/**
 * 终端守卫测试：死终端错误判定 + tmux 检测的非 tmux 路径 + 版本解析。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  checkTmuxExtendedKeys,
  checkTmuxKeyboardSetup,
  isDeadTerminalError,
  parseTmuxVersion,
} from '../../../../src/modes/tui/utils/terminal-guard.js';

describe('isDeadTerminalError', () => {
  it('EIO/EPIPE/ENOTCONN 判死；其余不误判', () => {
    for (const code of ['EIO', 'EPIPE', 'ENOTCONN']) {
      const error = new Error('x') as NodeJS.ErrnoException;
      error.code = code;
      assert.equal(isDeadTerminalError(error), true, code);
    }
    const other = new Error('x') as NodeJS.ErrnoException;
    other.code = 'ENOENT';
    assert.equal(isDeadTerminalError(other), false);
    assert.equal(isDeadTerminalError(new Error('plain')), false);
    assert.equal(isDeadTerminalError('string'), false);
    assert.equal(isDeadTerminalError(null), false);
  });
});

describe('checkTmuxKeyboardSetup', () => {
  it('非 tmux 环境返回 null', async () => {
    const saved = process.env.TMUX;
    try {
      delete process.env.TMUX;
      assert.equal(await checkTmuxKeyboardSetup(), null);
    } finally {
      if (saved !== undefined) process.env.TMUX = saved;
    }
  });
});

describe('parseTmuxVersion', () => {
  it('常规/前缀形态解析；垃圾输出 null', () => {
    assert.deepEqual(parseTmuxVersion('tmux 3.4'), [3, 4]);
    assert.deepEqual(parseTmuxVersion('tmux 3.1a'), [3, 1]);
    assert.deepEqual(parseTmuxVersion('tmux next-3.5'), [3, 5]);
    assert.deepEqual(parseTmuxVersion('tmux 2.9'), [2, 9]);
    assert.equal(parseTmuxVersion(''), null);
    assert.equal(parseTmuxVersion('not tmux'), null);
  });
});

describe('checkTmuxExtendedKeys', () => {
  it('非 tmux 环境返回 null', () => {
    const saved = process.env.TMUX;
    try {
      delete process.env.TMUX;
      assert.equal(checkTmuxExtendedKeys(), null);
    } finally {
      if (saved !== undefined) process.env.TMUX = saved;
    }
  });

  it('tmux 环境：< 3.1 警告升级；3.1+ / 探测失败 null', () => {
    const saved = process.env.TMUX;
    try {
      process.env.TMUX = '/tmp/tmux-test,123,0';
      assert.match(checkTmuxExtendedKeys(() => [3, 0])!, /< 3\.1/);
      assert.match(checkTmuxExtendedKeys(() => [2, 10])!, /extended-keys/);
      assert.equal(checkTmuxExtendedKeys(() => [3, 1]), null);
      assert.equal(checkTmuxExtendedKeys(() => [4, 0]), null);
      assert.equal(checkTmuxExtendedKeys(() => null), null); // 查不到不警告
    } finally {
      if (saved === undefined) delete process.env.TMUX;
      else process.env.TMUX = saved;
    }
  });
});
