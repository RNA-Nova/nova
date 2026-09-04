/**
 * 树选择器纯函数测试（bundle frontend/tui/extensions/session_commands/slash/tree/selector.ts——包侧组件）：组装规则（单子链平级/分支缩进/隐藏重挂/活跃分支优先）、
 * 折叠、标签派生、条目摘要、过滤五模式、搜索匹配。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  assembleTreeRows,
  deriveLabels,
  deriveLabelTimestamps,
  entryCopyText,
  formatLabelTimestamp,
  matchesQuery,
  summarizeEntry,
  type TreeEntry,
  type TreeView,
} from '../../../../../tui/extensions/session_commands/slash/tree/selector.js';

function msg(
  id: string,
  parentId: string | null,
  text: string,
  timestamp = '',
  role: 'user' | 'assistant' = 'user',
): TreeEntry {
  return {
    id,
    parentId,
    type: 'message',
    timestamp,
    message: { role, content: [{ type: 'text', text }] },
  };
}

function toolResult(id: string, parentId: string | null): TreeEntry {
  return {
    id,
    parentId,
    type: 'message',
    timestamp: '',
    message: { role: 'toolResult', content: 'ok' },
  };
}

function view(partial: Partial<TreeView> = {}): TreeView {
  return { filter: 'default', query: '', labels: new Map(), ...partial };
}

/** 线性链 a→b→c + 分支：b 下有 c 和 d 两个子级。 */
const BRANCHED: TreeEntry[] = [
  msg('a', null, '根', '2026-01-01T00:00:00'),
  msg('b', 'a', '第二', '2026-01-01T00:01:00'),
  msg('c', 'b', '分支一', '2026-01-01T00:02:00'),
  msg('d', 'b', '分支二', '2026-01-01T00:03:00'),
];

describe('assembleTreeRows', () => {
  it('单子链保持平级（无连接符），分支点才缩进', () => {
    const rows = assembleTreeRows(BRANCHED, 'c', new Set(), view());
    assert.equal(rows.length, 4);
    // a、b 平级无连接符；c、d 是 b 的分支子级，带 ├─/└─
    assert.equal(rows[0].prefix, '');
    assert.equal(rows[1].prefix, '');
    assert.match(rows[2].prefix, /├─/);
    assert.match(rows[3].prefix, /└─/);
  });

  it('活跃分支排在最前', () => {
    // leaf 在 d 分支：d（活跃）应排在 c 前
    const rows = assembleTreeRows(BRANCHED, 'd', new Set(), view());
    const branchRows = rows.filter((r) => r.prefix.includes('─'));
    assert.equal(branchRows[0].id, 'd');
    assert.equal(branchRows[1].id, 'c');
    // 活跃路径标记
    assert.ok(rows.find((r) => r.id === 'a')?.onActivePath);
    assert.ok(rows.find((r) => r.id === 'd')?.onActivePath);
    assert.ok(!rows.find((r) => r.id === 'c')?.onActivePath);
  });

  it('可折叠 = 有可见子级 且（根 或 分支子级）；折叠隐藏后代', () => {
    const rows = assembleTreeRows(BRANCHED, 'c', new Set(), view());
    // a：根且有子级 → 可折叠；b：单子链非根 → 不可折叠；c/d：无子级 → 不可折叠
    assert.ok(rows.find((r) => r.id === 'a')?.foldable);
    assert.ok(!rows.find((r) => r.id === 'b')?.foldable);
    assert.ok(!rows.find((r) => r.id === 'c')?.foldable);

    const folded = assembleTreeRows(BRANCHED, 'c', new Set(['a']), view());
    assert.equal(folded.length, 1); // 只剩 a
    assert.ok(folded[0].folded);
  });

  it('隐藏类型条目的后代重挂到最近可见祖先', () => {
    const entries: TreeEntry[] = [
      msg('a', null, '根'),
      // model_change 默认隐藏，其子 b 应重挂到 a 下
      { id: 'mc', parentId: 'a', type: 'model_change', timestamp: '' },
      msg('b', 'mc', '可见子'),
    ];
    const rows = assembleTreeRows(entries, 'b', new Set(), view());
    assert.deepEqual(
      rows.map((r) => r.id),
      ['a', 'b'],
    );
    // 重挂后 b 仍是单子链 → 平级无连接符
    assert.equal(rows[1].prefix, '');
  });

  it('label/session_info 等元条目不占行，label 派生进标签表', () => {
    const entries: TreeEntry[] = [
      msg('a', null, '根'),
      { id: 'l1', parentId: 'a', type: 'label', targetId: 'a', label: '里程碑', timestamp: '' },
      { id: 'si', parentId: 'a', type: 'session_info', timestamp: '' },
    ];
    const rows = assembleTreeRows(entries, 'a', new Set(), view());
    assert.equal(rows.length, 1);
    const labels = deriveLabels(entries);
    assert.equal(labels.get('a'), '里程碑');
    assert.ok(summarizeEntry(rows[0].entry, labels).startsWith('[里程碑]'));
  });

  it('空标签=删除（后写胜出）', () => {
    const entries: TreeEntry[] = [
      msg('a', null, '根'),
      { id: 'l1', parentId: 'a', type: 'label', targetId: 'a', label: 'x', timestamp: '' },
      { id: 'l2', parentId: 'a', type: 'label', targetId: 'a', label: '', timestamp: '' },
    ];
    assert.equal(deriveLabels(entries).has('a'), false);
  });
});

describe('过滤五模式', () => {
  const entries: TreeEntry[] = [
    msg('u1', null, '用户一'),
    msg('a1', 'u1', '助手一', '', 'assistant'),
    toolResult('tr1', 'a1'),
    { id: 'mc', parentId: 'tr1', type: 'model_change', timestamp: '' },
  ];

  it('default：消息全角色可见，元条目隐藏', () => {
    const rows = assembleTreeRows(entries, 'u1', new Set(), view());
    assert.deepEqual(
      rows.map((r) => r.id),
      ['u1', 'a1', 'tr1'],
    );
  });

  it('no-tools：再隐 toolResult（其后代重挂）', () => {
    const rows = assembleTreeRows(entries, 'u1', new Set(), view({ filter: 'no-tools' }));
    assert.deepEqual(
      rows.map((r) => r.id),
      ['u1', 'a1'],
    );
  });

  it('user-only：只剩用户消息', () => {
    const rows = assembleTreeRows(entries, 'u1', new Set(), view({ filter: 'user-only' }));
    assert.deepEqual(
      rows.map((r) => r.id),
      ['u1'],
    );
  });

  it('labeled-only：只剩有标签的条目', () => {
    const labels = new Map([['a1', '重点']]);
    const rows = assembleTreeRows(
      entries,
      'u1',
      new Set(),
      view({ filter: 'labeled-only', labels }),
    );
    assert.deepEqual(
      rows.map((r) => r.id),
      ['a1'],
    );
  });

  it('all：元条目也可见', () => {
    const rows = assembleTreeRows(entries, 'u1', new Set(), view({ filter: 'all' }));
    assert.deepEqual(
      rows.map((r) => r.id),
      ['u1', 'a1', 'tr1', 'mc'],
    );
  });
});

describe('搜索', () => {
  it('token AND 匹配（大小写不敏感）', () => {
    assert.ok(matchesQuery('Fix the LOGIN bug', 'login fix'));
    assert.ok(!matchesQuery('Fix the bug', 'login'));
    assert.ok(matchesQuery('anything', ''));
    assert.ok(matchesQuery('anything', '  '));
  });

  it('查询过滤行（不匹配行的后代重挂）', () => {
    const entries: TreeEntry[] = [
      msg('a', null, '部署指南'),
      msg('b', 'a', '无关内容'),
      msg('c', 'b', '部署细节'),
    ];
    const rows = assembleTreeRows(entries, 'a', new Set(), view({ query: '部署' }));
    assert.deepEqual(
      rows.map((r) => r.id),
      ['a', 'c'], // b 被滤掉，c 重挂到 a
    );
    assert.equal(rows[1].prefix, ''); // 单子链平级
  });
});

describe('summarizeEntry', () => {
  it('user 消息取首个非空行（控制字符归一）', () => {
    const entry = msg('a', null, '\n\n  第一行\n第二行  ');
    assert.equal(summarizeEntry(entry, new Map()), '第一行');
  });

  it('compaction/branch_summary 固定文案', () => {
    const base = { id: 'x', parentId: null, timestamp: '' };
    assert.equal(summarizeEntry({ ...base, type: 'compaction' }, new Map()), '[压缩]');
    assert.equal(summarizeEntry({ ...base, type: 'branch_summary' }, new Map()), '[分支摘要]');
  });
});

describe('标签时间戳与复制', () => {
  it('deriveLabelTimestamps：与标签同生命周期（空标签删除时间戳）', () => {
    const entries: TreeEntry[] = [
      msg('a', null, '根'),
      { id: 'l1', parentId: 'a', type: 'label', targetId: 'a', label: 'x', timestamp: '2026-08-11T10:00:00Z' },
      { id: 'l2', parentId: 'a', type: 'label', targetId: 'a', label: '', timestamp: '2026-08-11T11:00:00Z' },
    ];
    assert.equal(deriveLabelTimestamps(entries).has('a'), false);
    assert.equal(deriveLabelTimestamps(entries.slice(0, 2)).get('a'), '2026-08-11T10:00:00Z');
  });

  it('formatLabelTimestamp：当天 HH:MM / 今年 M/D / 跨年 YY/M/D', () => {
    const now = new Date('2026-08-11T15:00:00');
    assert.equal(formatLabelTimestamp('2026-08-11T09:05:00', now), '09:05');
    assert.equal(formatLabelTimestamp('2026-03-02T09:05:00', now), '3/2');
    assert.equal(formatLabelTimestamp('2024-03-02T09:05:00', now), '24/3/2');
    assert.equal(formatLabelTimestamp('not-a-date', now), '');
  });

  it('entryCopyText：消息取全文块拼接；摘要类取 summary', () => {
    const entry = msg('a', null, '第一行');
    (entry.message as { content: unknown }).content = [
      { type: 'text', text: '第一行' },
      { type: 'thinking', thinking: '嗯' },
      { type: 'text', text: '第二行' },
    ];
    assert.equal(entryCopyText(entry), '第一行\n第二行');
    const compaction: TreeEntry = {
      id: 'c', parentId: null, type: 'compaction', timestamp: '', summary: '压缩全文',
    };
    assert.equal(entryCopyText(compaction), '压缩全文');
    assert.equal(entryCopyText({ id: 'x', parentId: null, type: 'label', timestamp: '' }), '');
  });
});
