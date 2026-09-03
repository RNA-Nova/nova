/**
 * skill 条目测试：parseSkillBlock 解析 + UserMessageView 拆分渲染。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { parseSkillBlock } from '../../../../../src/modes/tui/components/transcript/skill-invocation.js';
import { UserMessageView } from '../../../../../src/modes/tui/components/transcript/user-message.js';
import type { ExpansionState } from '../../../../../src/modes/tui/components/transcript/expansion.js';

const SKILL_MESSAGE =
  '<skill name="path-helper" location="/tmp/skills/path-helper/SKILL.md">\n' +
  '如何处理路径。\n' +
  '</skill>';

describe('parseSkillBlock', () => {
  it('纯 skill 消息（无尾部文本）', () => {
    const parsed = parseSkillBlock(SKILL_MESSAGE);
    assert.ok(parsed);
    assert.equal(parsed.name, 'path-helper');
    assert.equal(parsed.location, '/tmp/skills/path-helper/SKILL.md');
    assert.equal(parsed.content, '如何处理路径。');
    assert.equal(parsed.userMessage, undefined);
  });

  it('skill + 尾部用户文本（\\n\\n 分隔）', () => {
    const parsed = parseSkillBlock(`${SKILL_MESSAGE}\n\n帮我看看这个目录`);
    assert.ok(parsed);
    assert.equal(parsed.name, 'path-helper');
    assert.equal(parsed.userMessage, '帮我看看这个目录');
  });

  it('普通文本 / 格式不符的 XML 不匹配', () => {
    assert.equal(parseSkillBlock('hello world'), null);
    assert.equal(parseSkillBlock('前置文本\n' + SKILL_MESSAGE), null); // 必须开头
    assert.equal(parseSkillBlock('<skill name="x">无 location</skill>'), null);
  });
});

describe('UserMessageView · skill 拆分', () => {
  const collapsed: ExpansionState = { expanded: false };
  const expanded: ExpansionState = { expanded: true };

  it('纯 skill 消息：折叠态单行 [skill] name（无 XML 全文）', () => {
    const out = new UserMessageView(SKILL_MESSAGE, collapsed).render(100).join('\n');
    assert.match(out, /\[skill\] path-helper/);
    assert.doesNotMatch(out, /如何处理路径/); // 折叠不显示全文
    assert.doesNotMatch(out, /<skill/); // XML 标记不外泄
  });

  it('展开态：[skill] + markdown 全文', () => {
    const out = new UserMessageView(SKILL_MESSAGE, expanded).render(100).join('\n');
    assert.match(out, /\[skill\]/);
    assert.match(out, /如何处理路径/);
  });

  it('skill + 尾部文本：skill 折叠 + 用户文本正常呈现', () => {
    const out = new UserMessageView(`${SKILL_MESSAGE}\n\n看看目录`, collapsed)
      .render(100)
      .join('\n');
    assert.match(out, /\[skill\] path-helper/);
    assert.match(out, /看看目录/);
  });

  it('普通消息不受影响', () => {
    const out = new UserMessageView('普通消息', collapsed).render(100).join('\n');
    assert.match(out, /普通消息/);
    assert.doesNotMatch(out, /\[skill\]/);
  });
});
