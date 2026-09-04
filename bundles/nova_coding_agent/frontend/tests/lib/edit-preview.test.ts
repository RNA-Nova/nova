/**
 * edit 执行前预览的匹配算法测试（与源文件同包就近 colocated）。
 *
 * 正确性攸关"预览 diff 与执行 diff 一致"——多级匹配（精确 → fuzzy 归一）、
 * 多 edit 反向应用、重叠/重复/无变化报错、只读全链路全部钉住。
 */
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, describe, it } from 'node:test';

import {
  applyEditsToNormalizedContent,
  computeEditPreview,
  fuzzyFindText,
  normalizeForFuzzyMatch,
} from '../../tui/lib/edit-preview.js';

const created: string[] = [];

after(async () => {
  for (const dir of created) await rm(dir, { recursive: true, force: true });
});

async function makeFile(lines: string[]): Promise<{ dir: string; path: string }> {
  const dir = await mkdtemp(join(tmpdir(), 'nova-edit-preview-test-'));
  created.push(dir);
  await writeFile(join(dir, 'demo.ts'), lines.join('\n') + '\n');
  return { dir, path: join(dir, 'demo.ts') };
}

describe('fuzzyFindText（两级匹配）', () => {
  it('精确匹配优先', () => {
    const result = fuzzyFindText('const a = 1;\nconst b = 2;\n', 'const b = 2;');
    assert.equal(result.found, true);
    assert.equal(result.usedFuzzyMatch, false);
    assert.equal(result.index, 13);
  });

  it('fuzzy：行尾空白与弯引号归一后能中', () => {
    const content = 'const msg = “hello”;  \n';
    const result = fuzzyFindText(content, 'const msg = "hello";');
    assert.equal(result.found, true);
    assert.equal(result.usedFuzzyMatch, true);
  });

  it('找不到：found=false', () => {
    assert.equal(fuzzyFindText('abc', 'xyz').found, false);
  });

  it('normalizeForFuzzyMatch：破折号与特殊空格归一', () => {
    assert.equal(normalizeForFuzzyMatch('a—b c'), 'a-b c');
  });
});

describe('applyEditsToNormalizedContent（多 edit 语义）', () => {
  it('多处不相交替换全部应用', () => {
    const { newContent } = applyEditsToNormalizedContent(
      'const a = 1;\nconst b = 2;\nconst c = 3;\n',
      [
        { oldText: 'const a = 1;', newText: 'const a = 10;' },
        { oldText: 'const c = 3;', newText: 'const c = 30;' },
      ],
      'demo.ts',
    );
    assert.equal(newContent, 'const a = 10;\nconst b = 2;\nconst c = 30;\n');
  });

  it('重叠替换抛错（防歧义应用）', () => {
    assert.throws(
      () =>
        applyEditsToNormalizedContent(
          'abcdef\n',
          [
            { oldText: 'abc', newText: 'x' },
            { oldText: 'bcd', newText: 'y' },
          ],
          'demo.ts',
        ),
      /overlap/,
    );
  });

  it('重复出现抛错（要求唯一匹配）', () => {
    assert.throws(
      () =>
        applyEditsToNormalizedContent('foo\nfoo\n', [{ oldText: 'foo', newText: 'bar' }], 'd.ts'),
      /occurrences/,
    );
  });

  it('无变化抛错（防静默空写）', () => {
    assert.throws(
      () => applyEditsToNormalizedContent('foo\n', [{ oldText: 'foo', newText: 'foo' }], 'd.ts'),
      /No changes/,
    );
  });
});

describe('computeEditPreview（只读全链路）', () => {
  it('产出标准 unified patch', async () => {
    const { dir, path } = await makeFile(['const a = 1;', 'const b = 2;']);
    const result = await computeEditPreview(
      path,
      [{ oldText: 'const b = 2;', newText: 'const b = 42;' }],
      dir,
    );
    assert.ok('patch' in result);
    if ('patch' in result) {
      assert.match(result.patch, /-const b = 2;/);
      assert.match(result.patch, /\+const b = 42;/);
    }
  });

  it('匹配失败返回 { error }（不 throw——预览通道失败即内容）', async () => {
    const { dir, path } = await makeFile(['const a = 1;']);
    const result = await computeEditPreview(
      path,
      [{ oldText: '不存在', newText: 'x' }],
      dir,
    );
    assert.ok('error' in result);
    if ('error' in result) assert.match(result.error, /Could not find/);
  });

  it('文件不存在返回 { error }', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'nova-edit-preview-test-'));
    created.push(dir);
    const result = await computeEditPreview(
      join(dir, 'ghost.ts'),
      [{ oldText: 'a', newText: 'b' }],
      dir,
    );
    assert.ok('error' in result);
  });

  it('fuzzy 命中：弯引号参数匹配直引号内容（预览与执行同语义）', async () => {
    const { dir, path } = await makeFile(['const msg = “hello”;']);
    const result = await computeEditPreview(
      path,
      [{ oldText: 'const msg = "hello";', newText: 'const msg = "bye";' }],
      dir,
    );
    assert.ok('patch' in result);
  });
});
