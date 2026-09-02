/**
 * 前端域路径族钉板（前后端分治 §9）：默认路径全部归 ``frontend/tui/`` 半区，
 * 后端状态根（settings/auth/sessions/packages 平级项）不被本层吞并。
 */

import assert from 'node:assert/strict';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { describe, it } from 'node:test';

import {
  projectFrontendDir,
  userAgentDir,
  userFrontendDir,
} from '../src/paths.js';
import { projectKeybindingsPath, userKeybindingsPath } from '../src/keymap/loader.js';
import { UISettings, UIStateStore } from '../src/settings/store.js';

describe('前端域路径族（§9）', () => {
  it('user 级前端域根：~/.nova/agent/frontend/tui', () => {
    assert.equal(userAgentDir(), join(homedir(), '.nova', 'agent'));
    assert.equal(
      userFrontendDir(),
      join(homedir(), '.nova', 'agent', 'frontend', 'tui'),
    );
  });

  it('project 级前端域根：<cwd>/.nova/frontend/tui', () => {
    assert.equal(
      projectFrontendDir('/work/proj'),
      join('/work/proj', '.nova', 'frontend', 'tui'),
    );
  });

  it('UISettings / UIStateStore 默认路径归前端域半区', () => {
    assert.equal(
      UISettings.defaultPath(),
      join(homedir(), '.nova', 'agent', 'frontend', 'tui', 'settings.json'),
    );
    assert.equal(
      UIStateStore.defaultDir(),
      join(homedir(), '.nova', 'agent', 'frontend', 'tui', 'state'),
    );
  });

  it('keybindings 两级路径归前端域半区', () => {
    assert.equal(
      userKeybindingsPath(),
      join(homedir(), '.nova', 'agent', 'frontend', 'tui', 'keybindings.json'),
    );
    assert.equal(
      projectKeybindingsPath('/work/proj'),
      join('/work/proj', '.nova', 'frontend', 'tui', 'keybindings.json'),
    );
  });
});
