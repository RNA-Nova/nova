/**
 * SearchableSelector 定制视觉测试：depth 树形缩进 + group 分组头。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { SearchableSelector, type SearchableItem } from '../../../../../src/modes/tui/components/pickers/searchable.js';

function render(selector: SearchableSelector): string {
  return selector.render(100).join('\n');
}

describe('SearchableSelector · 定制视觉', () => {
  it('depth：层级缩进（0 级无缩进，2 级四空格）', () => {
    const items: SearchableItem[] = [
      { value: 'root', label: '根节点', depth: 0 },
      { value: 'child', label: '子节点', depth: 1 },
      { value: 'grand', label: '孙节点', depth: 2 },
    ];
    const out = render(new SearchableSelector('树', items, {
      onSelect: () => {},
      onCancel: () => {},
    }));
    assert.match(out, /→ 根节点/); // 选中项 0 级无缩进
    assert.match(out, /\n\s+ {2}子节点/); // 1 级两空格
    assert.match(out, / {4}孙节点/); // 2 级四空格
  });

  it('group：组变化处插组头（同组不重复）', () => {
    const items: SearchableItem[] = [
      { value: 'a1', label: 'm1', group: 'provider-a' },
      { value: 'a2', label: 'm2', group: 'provider-a' },
      { value: 'b1', label: 'm3', group: 'provider-b' },
    ];
    const out = render(new SearchableSelector('模型', items, {
      onSelect: () => {},
      onCancel: () => {},
    }));
    // 两个组头各出现一次
    assert.equal(out.match(/provider-a/g)?.length, 1);
    assert.equal(out.match(/provider-b/g)?.length, 1);
    // 组头在组成员之前
    assert.ok(out.indexOf('provider-a') < out.indexOf('m1'));
    assert.ok(out.indexOf('provider-b') < out.indexOf('m3'));
  });

  it('无 depth/group 的条目渲染回归（普通列表无组头）', () => {
    const items: SearchableItem[] = [
      { value: 'x', label: 'plain', description: 'desc' },
    ];
    const out = render(new SearchableSelector('普通', items, {
      onSelect: () => {},
      onCancel: () => {},
    }));
    assert.match(out, /→ plain/);
    assert.match(out, /desc/);
  });
});
