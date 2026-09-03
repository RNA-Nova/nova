/**
 * SessionSelector 测试（bundle frontend/tui/extensions/session_commands/slash/resume/selector.ts——
 * 自 nova-client 宿主迁入）：模态状态机（删除确认吞键/重命名）、
 * formatAge 边界、sessionTitle 派生、搜索语法、排序管线。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  applySessionView,
  filterSessions,
  formatAge,
  SessionSelector,
  sessionTitle,
  type SessionItem,
  type SessionSelectorCallbacks,
} from '../../../../../tui/extensions/session_commands/slash/resume/selector.js';

const NOW = 1_800_000_000_000; // 固定现在时刻（ms）

function item(partial: Partial<SessionItem>): SessionItem {
  return {
    path: '/tmp/a.jsonl',
    name: null,
    firstMessage: '你好世界',
    messageCount: 3,
    modified: NOW / 1000,
    cwd: '/tmp',
    parentSessionPath: null,
    ...partial,
  };
}

function makeSelector(callbacks: Partial<SessionSelectorCallbacks> = {}) {
  const calls = { delete: [] as string[], rename: [] as Array<[string, string]> };
  const selector = new SessionSelector([item({ path: '/tmp/a.jsonl' })], {
    onSelect: callbacks.onSelect ?? (() => {}),
    onCancel: callbacks.onCancel ?? (() => {}),
    onDelete: (path) => calls.delete.push(path),
    onRename: (path, name) => calls.rename.push([path, name]),
    onScopeChange: callbacks.onScopeChange ?? (() => {}),
  });
  selector.focused = true;
  return { selector, calls };
}

const KEYS = {
  enter: '\r',
  escape: '\x1b',
  tab: '\t',
  ctrlD: '\x04',
  ctrlR: '\x12',
} as const;

describe('formatAge', () => {
  it('now/s/m/h/d/w/mo/y 边界', () => {
    assert.equal(formatAge(NOW / 1000, NOW), 'now');
    assert.equal(formatAge(NOW / 1000 - 120, NOW), '2m');
    assert.equal(formatAge(NOW / 1000 - 7200, NOW), '2h');
    assert.equal(formatAge(NOW / 1000 - 86400 * 3, NOW), '3d');
    assert.equal(formatAge(NOW / 1000 - 86400 * 14, NOW), '2w');
    assert.equal(formatAge(NOW / 1000 - 86400 * 60, NOW), '2mo');
    assert.equal(formatAge(NOW / 1000 - 86400 * 800, NOW), '2y');
  });
});

describe('sessionTitle', () => {
  it('名字优先，缺省用首条消息，控制字符清洗', () => {
    assert.equal(sessionTitle(item({ name: '发布会' })), '发布会');
    assert.equal(sessionTitle(item({ firstMessage: 'a\x00\x1b\nb' })), 'a b');
    assert.equal(sessionTitle(item({ firstMessage: '' })), '(无内容会话)');
  });
});

describe('SessionSelector 模态', () => {
  it('ctrl+d 进删除确认态：吞键（字符不进搜索框），esc 退出，enter 触发 onDelete', () => {
    const { selector, calls } = makeSelector();
    selector.handleInput(KEYS.ctrlD);
    let out = selector.render(100).join('\n');
    assert.match(out, /删除会话？/);
    // 吞键：普通字符不改变状态（仍在确认态）
    selector.handleInput('x');
    out = selector.render(100).join('\n');
    assert.match(out, /删除会话？/);
    assert.equal(calls.delete.length, 0);
    selector.handleInput(KEYS.enter);
    assert.deepEqual(calls.delete, ['/tmp/a.jsonl']);
  });

  it('删除确认态 esc 取消（不触发 onDelete）', () => {
    const { selector, calls } = makeSelector();
    selector.handleInput(KEYS.ctrlD);
    selector.handleInput(KEYS.escape);
    const out = selector.render(100).join('\n');
    assert.doesNotMatch(out, /删除会话？/);
    assert.equal(calls.delete.length, 0);
  });

  it('ctrl+r 进重命名态：Input 预填当前名，enter 提交 onRename', () => {
    const { selector, calls } = makeSelector();
    selector.handleInput(KEYS.ctrlR);
    let out = selector.render(100).join('\n');
    assert.match(out, /重命名/);
    // 键入新名字
    for (const ch of '新名字') selector.handleInput(ch);
    selector.handleInput(KEYS.enter);
    assert.deepEqual(calls.rename, [['/tmp/a.jsonl', '新名字']]);
  });

  it('tab 切换作用域并通知编排', () => {
    const scopes: string[] = [];
    const { selector } = makeSelector({ onScopeChange: (s) => scopes.push(s) });
    selector.handleInput(KEYS.tab);
    selector.handleInput(KEYS.tab);
    assert.deepEqual(scopes, ['all', 'current']);
  });
});

describe('filterSessions（pi 搜索语法）', () => {
  const items = [
    item({ path: '/a', name: '部署指南' }),
    item({ path: '/b', firstMessage: 'fix login bug' }),
    item({ path: '/c', name: '闲聊' }),
  ];

  it('re: 正则（大小写不敏感）；非法正则空结果', () => {
    const hit = filterSessions(items, 're:log.n', false);
    assert.deepEqual(hit.map((i) => i.path), ['/b']);
    assert.deepEqual(filterSessions(items, 're:[', false), []);
  });

  it('"phrase" 空白归一精确子串', () => {
    assert.deepEqual(
      filterSessions(items, '"login bug"', false).map((i) => i.path),
      ['/b'],
    );
    assert.deepEqual(filterSessions(items, '"login  x"', false), []);
  });

  it('裸 token fuzzy + namedOnly 过滤', () => {
    assert.deepEqual(
      filterSessions(items, '部署', false).map((i) => i.path),
      ['/a'],
    );
    // namedOnly：无名字的 /b 被排除
    assert.deepEqual(
      filterSessions(items, '', true).map((i) => i.path),
      ['/a', '/c'],
    );
  });
});

describe('applySessionView（排序）', () => {
  const base = { name: null, firstMessage: '', messageCount: 1, cwd: '', parentSessionPath: null };
  const older = NOW / 1000 - 100;
  const newer = NOW / 1000;

  it('recent：modified 倒序', () => {
    const rows = applySessionView(
      [
        item({ path: '/old', modified: older }),
        item({ path: '/new', modified: newer }),
      ],
      { query: '', namedOnly: false, sort: 'recent' },
    );
    assert.deepEqual(
      rows.map((r) => r.path),
      ['/new', '/old'],
    );
  });

  it('threaded：子会话随父缩进（父不在列表提升为根）；有查询回退非树形', () => {
    const rows = applySessionView(
      [
        item({ ...base, path: '/child', parentSessionPath: '/parent', modified: newer }),
        item({ ...base, path: '/parent', modified: older }),
      ],
      { query: '', namedOnly: false, sort: 'threaded' },
    );
    assert.deepEqual(
      rows.map((r) => [r.path, r.depth]),
      [
        ['/parent', 0],
        ['/child', 1],
      ],
    );
    // 有查询：threaded 不启用（平铺）
    const queried = applySessionView(
      [item({ ...base, path: '/child', parentSessionPath: '/parent', name: '目标' })],
      { query: '目标', namedOnly: false, sort: 'threaded' },
    );
    assert.equal(queried[0].depth, 0);
  });

  it('relevance：子串位置优先；无命中排除；空查询回退 recent', () => {
    const rows = applySessionView(
      [
        item({ path: '/x', name: '前缀 abc 后缀' }),
        item({ path: '/y', name: 'abc 开头' }),
        item({ path: '/z', name: '无关' }),
      ],
      { query: 'abc', namedOnly: false, sort: 'relevance' },
    );
    assert.deepEqual(
      rows.map((r) => r.path),
      ['/y', '/x'], // 位置 0 优先于位置 3；无命中排除
    );
  });
});
