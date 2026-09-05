/**
 * resolveBackendCommand（wire/backend-command.ts）测试：
 * 解析链 NOVA_BACKEND > 同目录 runtime/nova-server > NOVA_PYTHON > python3 模块调用。
 */

import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, it } from 'node:test';

import { resolveBackendCommand } from '../../src/wire/backend-command.js';

describe('resolveBackendCommand 解析链', () => {
  it('NOVA_BACKEND 显式指定优先一切', () => {
    const cmd = resolveBackendCommand({ NOVA_BACKEND: '/custom/nova-server' }, '/nonexistent');
    assert.deepEqual(cmd, ['/custom/nova-server']);
  });

  it('同目录 runtime/nova-server 存在即用（打包形态）', () => {
    const execDir = mkdtempSync(join(tmpdir(), 'nova-backend-'));
    mkdirSync(join(execDir, 'runtime'));
    writeFileSync(join(execDir, 'runtime', 'nova-server'), '');
    const cmd = resolveBackendCommand({}, execDir);
    assert.deepEqual(cmd, [join(execDir, 'runtime', 'nova-server')]);
  });

  it('Windows 形态找 nova-server.exe', () => {
    const execDir = mkdtempSync(join(tmpdir(), 'nova-backend-win-'));
    mkdirSync(join(execDir, 'runtime'));
    writeFileSync(join(execDir, 'runtime', 'nova-server.exe'), '');
    const cmd = resolveBackendCommand({}, execDir, 'win32');
    assert.deepEqual(cmd, [join(execDir, 'runtime', 'nova-server.exe')]);
  });

  it('无二进制时 NOVA_PYTHON 指定解释器', () => {
    const cmd = resolveBackendCommand({ NOVA_PYTHON: '/envs/dev/bin/python' }, '/nonexistent');
    assert.deepEqual(cmd, ['/envs/dev/bin/python', '-m', 'nova_harness.modes.rpc.cli']);
  });

  it('全部缺省回落 python3 模块调用', () => {
    const cmd = resolveBackendCommand({}, '/nonexistent');
    assert.deepEqual(cmd, ['python3', '-m', 'nova_harness.modes.rpc.cli']);
  });
});
