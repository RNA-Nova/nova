/**
 * /model 编排纯函数测试（frontend/tui/extensions/session_commands/slash/model/controller.ts）：
 * buildModelItems——当前模型置顶带 ✓、scoped 档过滤、元信息描述列、provider 分组。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  buildModelItems,
  modelKey,
  type ModelListItem,
  type ScopedModelItem,
} from '../../../../../tui/extensions/session_commands/slash/model/controller.js';

function model(partial: Partial<ModelListItem>): ModelListItem {
  return {
    provider: 'openai',
    id: 'gpt-4o',
    name: 'GPT-4o',
    available: true,
    reasoning: false,
    ...partial,
  };
}

const MODELS: ModelListItem[] = [
  model({ provider: 'openai', id: 'gpt-4o', name: 'GPT-4o' }),
  model({ provider: 'volcengine', id: 'deepseek', name: 'DeepSeek', reasoning: true }),
  model({ provider: 'volcengine', id: 'doubao', name: 'Doubao', available: false }),
];

describe('modelKey', () => {
  it('provider/id 拼接', () => {
    assert.equal(modelKey({ provider: 'openai', id: 'gpt-4o' }), 'openai/gpt-4o');
  });
});

describe('buildModelItems', () => {
  it('当前模型置顶带 ✓，其余保持后端顺序', () => {
    const items = buildModelItems(MODELS, { provider: 'volcengine', id: 'deepseek' }, [], 'all');
    assert.deepEqual(
      items.map((item) => item.value),
      ['volcengine/deepseek', 'openai/gpt-4o', 'volcengine/doubao'],
    );
    assert.match(items[0].label, /^✓ /);
    assert.doesNotMatch(items[1].label, /✓/);
  });

  it('无当前模型：保持后端顺序，无 ✓', () => {
    const items = buildModelItems(MODELS, null, [], 'all');
    assert.deepEqual(
      items.map((item) => item.value),
      ['openai/gpt-4o', 'volcengine/deepseek', 'volcengine/doubao'],
    );
    assert.ok(items.every((item) => !item.label.includes('✓')));
  });

  it('scoped 档：仅池中模型（当前模型置顶规则不变）', () => {
    const scoped: ScopedModelItem[] = [
      { provider: 'volcengine', id: 'deepseek', thinkingLevel: null },
      { provider: 'openai', id: 'gpt-4o', thinkingLevel: 'high' },
    ];
    const items = buildModelItems(
      MODELS,
      { provider: 'openai', id: 'gpt-4o' },
      scoped,
      'scoped',
    );
    assert.deepEqual(
      items.map((item) => item.value),
      ['openai/gpt-4o', 'volcengine/deepseek'],
    );
  });

  it('元信息描述列：name · reasoning · 未配置凭据', () => {
    const items = buildModelItems(MODELS, null, [], 'all');
    const byValue = new Map(items.map((item) => [item.value, item]));
    assert.equal(byValue.get('openai/gpt-4o')?.description, 'GPT-4o');
    assert.equal(byValue.get('volcengine/deepseek')?.description, 'DeepSeek · reasoning');
    assert.equal(byValue.get('volcengine/doubao')?.description, 'Doubao · 未配置凭据');
  });

  it('provider 分组（选择器按组渲染组头）', () => {
    const items = buildModelItems(MODELS, null, [], 'all');
    assert.deepEqual(
      items.map((item) => item.group),
      ['openai', 'volcengine', 'volcengine'],
    );
  });
});
