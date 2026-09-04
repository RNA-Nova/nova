/**
 * P3-2 测试：thinking 边框色（ThemeFace.thinkingBorderColor）+
 * AssistantView thinking 显隐渲染。
 */

import assert from 'node:assert/strict';
import { before, describe, it } from 'node:test';

import chalk from 'chalk';

import { createThemeFace } from '../../../../../src/modes/tui/themes/theme.js';
import { BUILTIN_DARK } from '../../../../../src/modes/tui/themes/builtin-dark.js';
import { AssistantView } from '../../../../../src/modes/tui/components/transcript/assistant-message.js';

before(() => {
  chalk.level = 3; // 非 TTY 环境强制上色（ansi 断言有效）
});

describe('thinkingBorderColor', () => {
  const face = createThemeFace(BUILTIN_DARK);

  it('各级别取主题 token（dark：off→darkGray #505050，xhigh→#d183e8）', () => {
    assert.equal(face.thinkingBorderColor('off')('x'), `\x1b[38;2;80;80;80mx\x1b[39m`);
    assert.equal(face.thinkingBorderColor('xhigh')('x'), `\x1b[38;2;209;131;232mx\x1b[39m`);
  });

  it('max 缺省回退 xhigh；未知级别回退 borderMuted', () => {
    // dark 显式定义了 thinkingMax=#ff5fff
    assert.equal(face.thinkingBorderColor('max')('x'), `\x1b[38;2;255;95;255mx\x1b[39m`);
    // 未知级别 → borderMuted（darkGray #505050——与 off 同色不同源）
    assert.equal(face.thinkingBorderColor('bogus')('x'), `\x1b[38;2;80;80;80mx\x1b[39m`);
  });

  it('主题缺 thinking token 时回退 borderMuted', () => {
    const minimal = {
      name: 'bare',
      vars: BUILTIN_DARK.vars,
      colors: Object.fromEntries(
        Object.entries(BUILTIN_DARK.colors).filter(([key]) => !key.startsWith('thinking')),
      ),
    } as typeof BUILTIN_DARK;
    const bare = createThemeFace(minimal);
    assert.equal(bare.thinkingBorderColor('high')('x'), bare.colors.borderMuted('x'));
  });
});

describe('AssistantView · thinking 显隐', () => {
  it('缺省显示 thinking 全文（斜体区块）', () => {
    const view = new AssistantView('正文', '推理过程', undefined, undefined, () => false);
    const out = view.render(80).join('\n');
    assert.match(out, /推理过程/);
    assert.doesNotMatch(out, /Thinking\.\.\./);
  });

  it('hideThinking=true：折叠为静态标签（全文不出现）', () => {
    const view = new AssistantView('正文', '推理过程', undefined, undefined, () => true);
    const out = view.render(80).join('\n');
    assert.match(out, /Thinking\.\.\./);
    assert.doesNotMatch(out, /推理过程/);
    assert.match(out, /正文/); // 正文不受影响
  });

  it('无 thinking 时 hideThinking 无视觉差异', () => {
    const view = new AssistantView('只有正文', undefined, undefined, undefined, () => true);
    const out = view.render(80).join('\n');
    assert.match(out, /只有正文/);
    assert.doesNotMatch(out, /Thinking\.\.\./);
  });
});
