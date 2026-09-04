/**
 * 启动编排测试：flags 解析纯函数（@file 展开/初始消息/thinking/compaction 计数）
 * + StartupController 的一次性编排（fake runtime/transcript/sessions）。
 */

import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { describe, it } from 'node:test';

import type { RuntimeHost } from '../../runtime.js';
import type { SessionSnapshot } from '../../mirror/types.js';
import {
  buildInitialMessage,
  countCompactionEntries,
  detectImageMimeType,
  expandFileArguments,
  expandTildePath,
  formatCompactionHint,
  isValidThinkingLevel,
  resolveSessionArg,
  splitMessageTokens,
  StartupController,
  StartupError,
  TRUST_BANNER_TEXT,
  type StartupControllerDeps,
} from '../../../../src/modes/tui/controllers/startup.js';

describe('splitMessageTokens', () => {
  it('@前缀词元归入文件参数；其余为消息', () => {
    assert.deepEqual(splitMessageTokens(['hello', '@a.md', 'world', '@dir/b.txt']), {
      messageTokens: ['hello', 'world'],
      fileArgs: ['a.md', 'dir/b.txt'],
    });
  });

  it('孤立 @ 按字面文本；空输入', () => {
    assert.deepEqual(splitMessageTokens(['@', 'x']), { messageTokens: ['@', 'x'], fileArgs: [] });
    assert.deepEqual(splitMessageTokens([]), { messageTokens: [], fileArgs: [] });
  });
});

describe('expandTildePath', () => {
  it('~ 与 ~/ 展开；其余原样', () => {
    const home = process.env.HOME ?? tmpdir();
    assert.equal(expandTildePath('~'), home);
    assert.equal(expandTildePath('~/x/y.md'), join(home, 'x/y.md'));
    assert.equal(expandTildePath('/abs/p.md'), '/abs/p.md');
    assert.equal(expandTildePath('~other/p.md'), '~other/p.md'); // 仅 ~/ 展开
  });
});

describe('resolveSessionArg', () => {
  it('路径形态解析为绝对路径；裸 id 原样透传', () => {
    const cwd = '/work/proj';
    // 期望值经 resolve 现算——路径分隔符与盘符语义分平台（POSIX 绝对路径
    // 原样返回；Windows 补当前盘符），本用例钉的是"路径形态进 resolve、
    // 裸 id 透传"的分类行为
    assert.equal(resolveSessionArg('/abs/s.jsonl', cwd), resolve(cwd, '/abs/s.jsonl'));
    assert.equal(resolveSessionArg('rel/s.jsonl', cwd), resolve(cwd, 'rel/s.jsonl'));
    assert.equal(resolveSessionArg('s.jsonl', cwd), resolve(cwd, 's.jsonl')); // .jsonl 后缀视为路径
    assert.equal(resolveSessionArg('abc123', cwd), 'abc123'); // 裸 id 由后端解析
    assert.equal(resolveSessionArg('a\\b.jsonl', cwd), resolve(cwd, 'a\\b.jsonl'));
  });
});

describe('isValidThinkingLevel', () => {
  it('契约枚举全收；非法值拒', () => {
    for (const level of ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']) {
      assert.equal(isValidThinkingLevel(level), true, level);
    }
    assert.equal(isValidThinkingLevel('HIGH'), false);
    assert.equal(isValidThinkingLevel(''), false);
  });
});

describe('expandFileArguments', () => {
  it('文本文件包为 <file> 块；空文件跳过', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'nova-startup-'));
    try {
      await writeFile(join(dir, 'a.txt'), 'hello\nworld\n', 'utf-8');
      await writeFile(join(dir, 'empty.md'), '', 'utf-8');
      const { text, images } = await expandFileArguments(['a.txt', 'empty.md'], dir);
      assert.equal(text, `<file name="${join(dir, 'a.txt')}">\nhello\nworld\n\n</file>\n`);
      assert.deepEqual(images, []);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it('文件不存在 → StartupError（消息含绝对路径与原 @参数）', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'nova-startup-'));
    try {
      await assert.rejects(
        expandFileArguments(['missing.md'], dir),
        (error: unknown) => {
          assert.ok(error instanceof StartupError);
          assert.match(error.message, /文件不存在/);
          assert.match(error.message, /missing\.md/);
          return true;
        },
      );
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it('@参数指向目录 → StartupError', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'nova-startup-'));
    try {
      await mkdir(join(dir, 'sub'));
      await assert.rejects(expandFileArguments(['sub'], dir), /目录而非文件/);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});

describe('expandFileArguments 图片附件', () => {
  it('PNG 魔数命中 → ImageContent 附件 + 空体 <file> 引用', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'nova-startup-'));
    try {
      const png = Buffer.concat([
        Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
        Buffer.alloc(32, 7),
      ]);
      await writeFile(join(dir, 'shot.png'), png);
      const { text, images } = await expandFileArguments(['shot.png'], dir);
      assert.equal(images.length, 1);
      assert.equal(images[0].type, 'image');
      assert.equal(images[0].mimeType, 'image/png');
      assert.equal(Buffer.from(images[0].data, 'base64').length, png.length);
      assert.equal(text, `<file name="${join(dir, 'shot.png')}"></file>\n`);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it('JPEG/GIF/WebP 嗅探（detectImageMimeType 全格式面）', () => {
    assert.equal(detectImageMimeType(Uint8Array.from([0xff, 0xd8, 0xff, 0xe0])), 'image/jpeg');
    assert.equal(detectImageMimeType(Uint8Array.from([0x47, 0x49, 0x46, 0x38, 0x39, 0x61])), 'image/gif');
    assert.equal(
      detectImageMimeType(
        Uint8Array.from([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]),
      ),
      'image/webp',
    );
    assert.equal(detectImageMimeType(Uint8Array.from([0x6e, 0x6f, 0x70, 0x65])), null);
  });

  it('文本与图片混排：文本内联、图片成附件', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'nova-startup-'));
    try {
      await writeFile(join(dir, 'a.txt'), 'hello', 'utf-8');
      await writeFile(join(dir, 'b.jpg'), Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3]));
      const { text, images } = await expandFileArguments(['a.txt', 'b.jpg'], dir);
      assert.equal(images.length, 1);
      assert.equal(images[0].mimeType, 'image/jpeg');
      assert.ok(text.includes('hello'));
      assert.ok(text.includes(`<file name="${join(dir, 'b.jpg')}"></file>`));
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});

describe('buildInitialMessage', () => {
  it('文件块在前消息在后；全空返回 undefined', () => {
    assert.equal(buildInitialMessage('', []), undefined);
    assert.equal(buildInitialMessage('', ['hi']), 'hi');
    assert.equal(buildInitialMessage('', ['a', 'b']), 'a b');
    assert.equal(buildInitialMessage('<file name="/x">\nc\n</file>\n', []), '<file name="/x">\nc\n</file>');
    assert.equal(buildInitialMessage('<file name="/x">\nc\n</file>\n', ['go']), '<file name="/x">\nc\n</file>\ngo');
  });
});

describe('countCompactionEntries', () => {
  it('只数 type === compaction；异常条目不炸', () => {
    assert.equal(
      countCompactionEntries([
        { type: 'compaction' },
        { type: 'message' },
        null,
        'x',
        { type: 'compaction' },
        {},
      ]),
      2,
    );
    assert.equal(countCompactionEntries([]), 0);
  });
});

describe('formatCompactionHint', () => {
  it('计数进文案', () => {
    assert.equal(formatCompactionHint(3), '会话已压缩 3 次');
  });
});

// ------------------------------------------------------------------
// StartupController（fake 依赖）
// ------------------------------------------------------------------

interface InvokeCall {
  method: string;
  params: unknown;
}

function createFakeDeps(options?: {
  entries?: unknown[];
  throwOn?: string;
  withSessions?: boolean;
}) {
  const invokeCalls: InvokeCall[] = [];
  const infos: string[] = [];
  let sessionOpened = 0;
  const runtime = {
    invoke: (async (method: string, params?: unknown) => {
      invokeCalls.push({ method, params });
      if (options?.throwOn === method) throw new Error('boom');
      if (method === 'getSessionEntries') return { entries: options?.entries ?? [] };
      if (method === 'setSessionName') return { ok: true, name: (params as { name: string }).name };
      return {};
    }) as unknown as Pick<RuntimeHost, 'invoke'>['invoke'],
  };
  const deps: StartupControllerDeps = {
    runtime: { invoke: runtime.invoke },
    transcript: { addInfo: (message: string) => infos.push(message) },
    ...(options?.withSessions
      ? {
          sessions: {
            open: async () => {
              sessionOpened += 1;
            },
          },
        }
      : {}),
  };
  return { deps, invokeCalls, infos, opened: () => sessionOpened };
}

describe('StartupController.showTrustBannerIfNeeded', () => {
  it('projectTrusted === false → 横幅；true/null → 无', () => {
    const { deps, infos } = createFakeDeps();
    const controller = new StartupController(deps);
    controller.showTrustBannerIfNeeded({ projectTrusted: true });
    controller.showTrustBannerIfNeeded(null);
    assert.deepEqual(infos, []);
    controller.showTrustBannerIfNeeded({ projectTrusted: false });
    assert.deepEqual(infos, [TRUST_BANNER_TEXT]);
  });
});

describe('StartupController.showCompactionHintIfNeeded', () => {
  it('N>0 → 提示一行；N=0 → 无提示', async () => {
    const { deps, infos } = createFakeDeps({
      entries: [{ type: 'compaction' }, { type: 'message' }, { type: 'compaction' }],
    });
    const controller = new StartupController(deps);
    await controller.showCompactionHintIfNeeded();
    assert.deepEqual(infos, ['会话已压缩 2 次']);

    const empty = createFakeDeps({ entries: [] });
    await new StartupController(empty.deps).showCompactionHintIfNeeded();
    assert.deepEqual(empty.infos, []);
  });

  it('拉取失败静默（提示性能力不阻断启动）', async () => {
    const { deps, infos } = createFakeDeps({ throwOn: 'getSessionEntries' });
    await new StartupController(deps).showCompactionHintIfNeeded();
    assert.deepEqual(infos, []);
  });
});

describe('StartupController.applySessionNameIfNeeded', () => {
  it('有 name → setSessionName；无 name → 不调用', async () => {
    const named = createFakeDeps();
    await new StartupController(named.deps, { sessionName: '重构 auth' }).applySessionNameIfNeeded();
    assert.deepEqual(named.invokeCalls, [{ method: 'setSessionName', params: { name: '重构 auth' } }]);

    const unnamed = createFakeDeps();
    await new StartupController(unnamed.deps).applySessionNameIfNeeded();
    assert.deepEqual(unnamed.invokeCalls, []);
  });

  it('命名失败仅提示不抛出', async () => {
    const { deps, infos } = createFakeDeps({ throwOn: 'setSessionName' });
    await new StartupController(deps, { sessionName: 'x' }).applySessionNameIfNeeded();
    assert.deepEqual(infos, ['设置会话名失败：boom']);
  });
});

describe('StartupController.openResumeSelectorIfRequested', () => {
  it('resume + sessions → 打开；缺其一 → 不打开', async () => {
    const withBoth = createFakeDeps({ withSessions: true });
    await new StartupController(withBoth.deps, { resume: true }).openResumeSelectorIfRequested();
    assert.equal(withBoth.opened(), 1);

    const noSessions = createFakeDeps();
    await new StartupController(noSessions.deps, { resume: true }).openResumeSelectorIfRequested();

    const noResume = createFakeDeps({ withSessions: true });
    await new StartupController(noResume.deps).openResumeSelectorIfRequested();
    assert.equal(noResume.opened(), 0);
  });
});

describe('StartupController.runPostStart', () => {
  it('横幅 → 压缩提示 → 命名 → resume 选择器（全链路）', async () => {
    const { deps, invokeCalls, infos, opened } = createFakeDeps({
      entries: [{ type: 'compaction' }],
      withSessions: true,
    });
    const controller = new StartupController(deps, { sessionName: 'n', resume: true });
    await controller.runPostStart({ projectTrusted: false } as SessionSnapshot);
    assert.deepEqual(infos, [TRUST_BANNER_TEXT, '会话已压缩 1 次']);
    assert.deepEqual(invokeCalls, [
      { method: 'getSessionEntries', params: {} },
      { method: 'setSessionName', params: { name: 'n' } },
    ]);
    assert.equal(opened(), 1);
  });
});
