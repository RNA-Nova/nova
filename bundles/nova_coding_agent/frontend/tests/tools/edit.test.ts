/**
 * edit 渲染器测试（frontend/tui/tools/edit.ts）：
 * details.patch 走 unified patch 解析（增删行计数、hunk 头与上下文行）、
 * patch 缺失时降级 old/new 整体替换、执行前预览（input.preview 通道
 * 与 preview 命名导出钩子的成功/失败路径）、错误回执。
 * diff 行染色走宿主主题（chalk），断言前统一剥 ANSI 以防 TTY 环境差异。
 */

import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, it } from 'node:test';

import renderEdit, { preview } from '../../tui/tools/edit.js';

const identity = (s: string) => s;
const env = {
  cwd: '/tmp',
  colors: new Proxy({}, { get: () => identity }) as Record<string, (s: string) => string>,
  expanded: false,
};

const stripAnsi = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, '');

/** 渲染并归一为 trim 后的纯文本行（染色/宽度填充不影响断言）。 */
function renderTrimmed(output: unknown, width = 100): string[] {
  assert.ok(
    typeof output === 'object' && output !== null && typeof (output as any).render === 'function',
    '渲染器应产出组件形态',
  );
  return (output as { render: (w: number) => string[] })
    .render(width)
    .map((l) => stripAnsi(l).trim())
    .filter((l) => l.length > 0);
}

function editInput(overrides: Record<string, unknown>) {
  return { toolName: 'edit', status: 'done' as const, env, ...overrides } as any;
}

describe('edit 渲染器（details.patch 通道）', () => {
  it('消费标准 unified patch：hunk 头 + 上下文 + 1 删 1 增', () => {
    const patch = [
      '--- a/f.ts',
      '+++ b/f.ts',
      '@@ -1,2 +1,2 @@',
      ' const a = 1;',
      '-const b = 2;',
      '+const b = 42;',
    ].join('\n');
    const trimmed = renderTrimmed(
      renderEdit(editInput({ result: { details: { path: 'f.ts', patch } } })),
    );
    assert.deepEqual(trimmed, ['@@ -1,2 +1,2 @@', 'const a = 1;', '- const b = 2;', '+ const b = 42;']);
  });

  it('多行增删计数正确（2 删 1 增整行平铺）', () => {
    const patch = ['@@ -1,3 +1,2 @@', ' line1', '-a', '-b', '+c'].join('\n');
    const trimmed = renderTrimmed(renderEdit(editInput({ result: { details: { path: 'f.ts', patch } } })));
    assert.deepEqual(trimmed, ['@@ -1,3 +1,2 @@', 'line1', '- a', '- b', '+ c']);
    assert.equal(trimmed.filter((l) => l.startsWith('- ')).length, 2);
    assert.equal(trimmed.filter((l) => l.startsWith('+ ')).length, 1);
  });

  it('patch 解析不出 hunk 时落空，降级 old/new 整体替换', () => {
    const trimmed = renderTrimmed(
      renderEdit(
        editInput({
          result: { details: { path: 'g.ts', patch: 'no hunks here', old: 'foo\nbar', new: 'baz\nbar' } },
        }),
      ),
    );
    // 降级路径：旧内容全删行在前，新内容全增行在后（单 hunk 无头）
    assert.deepEqual(trimmed, ['- foo', '- bar', '+ baz', '+ bar']);
  });

  it('patch 缺失：old/new 整体替换', () => {
    const trimmed = renderTrimmed(
      renderEdit(editInput({ result: { details: { path: 'g.ts', old: 'foo\nbar', new: 'baz\nbar' } } })),
    );
    assert.deepEqual(trimmed, ['- foo', '- bar', '+ baz', '+ bar']);
  });

  it('错误回执标错', () => {
    const trimmed = renderTrimmed(renderEdit(editInput({ result: { details: { error: 'boom' } } })));
    assert.deepEqual(trimmed, ['编辑失败：boom']);
  });
});

describe('edit 渲染器（执行前预览 input.preview 通道）', () => {
  it('preview.patch：预览标记 + diff 行', () => {
    const patch = ['@@ -1,1 +1,1 @@', '-old', '+new'].join('\n');
    const trimmed = renderTrimmed(
      renderEdit(
        editInput({
          status: 'streaming',
          args: { path: 'f.ts', edits: [{ oldText: 'old', newText: 'new' }] },
          preview: { patch, path: 'f.ts' },
        }),
      ),
    );
    assert.deepEqual(trimmed, ['预览（尚未执行）', '@@ -1,1 +1,1 @@', '- old', '+ new']);
  });

  it('preview.error：预览匹配失败行', () => {
    const trimmed = renderTrimmed(
      renderEdit(
        editInput({
          status: 'streaming',
          args: { path: 'f.ts', edits: [{ oldText: 'x', newText: 'y' }] },
          preview: { error: 'Could not find the exact text in f.ts.' },
        }),
      ),
    );
    assert.ok(trimmed[0]?.startsWith('预览匹配失败：Could not find'));
  });

  it('执行后 details.patch 优先于 preview', () => {
    const executed = ['@@ -1,1 +1,1 @@', '-a', '+b'].join('\n');
    const stale = ['@@ -1,1 +1,1 @@', '-x', '+y'].join('\n');
    const trimmed = renderTrimmed(
      renderEdit(
        editInput({
          result: { details: { path: 'f.ts', patch: executed } },
          preview: { patch: stale, path: 'f.ts' },
        }),
      ),
    );
    assert.deepEqual(trimmed, ['@@ -1,1 +1,1 @@', '- a', '+ b']);
  });
});

describe('edit preview 命名导出（只读预览钩子）', () => {
  const created: string[] = [];
  after(async () => {
    for (const dir of created) await rm(dir, { recursive: true, force: true });
  });
  async function makeFile(lines: string[]): Promise<{ dir: string; path: string }> {
    const dir = await mkdtemp(join(tmpdir(), 'nova-edit-renderer-test-'));
    created.push(dir);
    await writeFile(join(dir, 'demo.ts'), lines.join('\n') + '\n');
    return { dir, path: join(dir, 'demo.ts') };
  }

  it('参数不完整返回 undefined（path 缺失 / edits 为空）', async () => {
    assert.equal(await preview({}, '/tmp'), undefined);
    assert.equal(await preview({ path: 'f.ts' }, '/tmp'), undefined);
    assert.equal(await preview({ path: 'f.ts', edits: [] }, '/tmp'), undefined);
  });

  it('引擎匹配成功：产出 { patch, path }', async () => {
    const { dir, path } = await makeFile(['const a = 1;', 'const b = 2;']);
    const result = (await preview(
      { path, edits: [{ oldText: 'const b = 2;', newText: 'const b = 42;' }] },
      dir,
    )) as { patch: string; path: string };
    assert.ok(typeof result.patch === 'string');
    assert.match(result.patch, /-const b = 2;/);
    assert.match(result.patch, /\+const b = 42;/);
    assert.equal(result.path, path);
  });

  it('找不到 oldText：返回 { error }（不 throw——预览通道失败即内容）', async () => {
    const { dir, path } = await makeFile(['const a = 1;']);
    const result = (await preview({ path, edits: [{ oldText: '不存在', newText: 'x' }] }, dir)) as {
      error: string;
    };
    assert.match(result.error, /Could not find/);
  });
});
