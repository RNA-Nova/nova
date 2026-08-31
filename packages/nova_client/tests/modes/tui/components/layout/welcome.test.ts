/**
 * WelcomeView 启动区测试：版本/模型/cwd/键位提示渲染与两态切换。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { WelcomeView } from '../../../../../src/modes/tui/components/layout/welcome.js';
import type { ExpansionState } from '../../../../../src/modes/tui/components/transcript/expansion.js';

function renderLines(view: WelcomeView): string {
  // Container.render(width) 产出行数组；chalk level=0（非 TTY）即纯文本
  return view.render(100).join('\n');
}

describe('WelcomeView', () => {
  it('compact 态：logo+版本、模型+cwd、紧凑键位提示', () => {
    const expansion: ExpansionState = { expanded: false };
    const view = new WelcomeView({
      version: '0.2.0',
      cwd: '/tmp/proj',
      model: () => 'volcengine/deepseek-v3',
      expansion,
    });
    const out = renderLines(view);
    assert.match(out, /nova v0\.2\.0/);
    assert.match(out, /volcengine\/deepseek-v3 · \/tmp\/proj/);
    assert.match(out, /命令/);
    assert.match(out, /bash/);
    assert.doesNotMatch(out, /中断运行/); // expanded 全量键位不出现
  });

  it('expanded 态：全量键位表', () => {
    const expansion: ExpansionState = { expanded: true };
    const view = new WelcomeView({
      version: '0.2.0',
      cwd: '/tmp/proj',
      model: () => undefined,
      expansion,
    });
    const out = renderLines(view);
    assert.match(out, /中断运行/);
    assert.match(out, /粘贴（图片/);
    assert.match(out, /\/theme/);
    assert.match(out, /未配置模型/); // model getter 空兜底
  });

  it('refresh：模型切换后重渲染取新值', () => {
    const expansion: ExpansionState = { expanded: false };
    let model: string | undefined = 'a/m1';
    const view = new WelcomeView({
      version: '0.2.0',
      cwd: '/tmp/proj',
      model: () => model,
      expansion,
    });
    assert.match(renderLines(view), /a\/m1/);
    model = 'b/m2';
    view.refresh();
    assert.match(renderLines(view), /b\/m2/);
  });
});
