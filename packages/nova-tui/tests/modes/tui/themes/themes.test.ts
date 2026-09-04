/**
 * themes/ 主题系统测试：JSON 校验、vars 解析、ThemeFace 生成、
 * 单例切换、自定义目录发现、Proxy 活引用。
 */

import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, describe, it } from 'node:test';

import chalk from 'chalk';

import {
  bindTerminalThemeSync,
  colors,
  getAvailableThemes,
  getCurrentThemeName,
  getExportThemeData,
  initTheme,
  onThemeChange,
  parseThemeJson,
  registerPackageThemePaths,
  registerPackageThemes,
  setCustomThemesDirForTest,
  setTheme,
  watchThemeFiles,
} from '../../../../src/modes/tui/themes/index.js';
import { createThemeFace } from '../../../../src/modes/tui/themes/theme.js';
import { BUILTIN_DARK } from '../../../../src/modes/tui/themes/builtin-dark.js';
import { BUILTIN_LIGHT } from '../../../../src/modes/tui/themes/builtin-light.js';
import { resolveThemeColors, type ThemeJson } from '../../../../src/modes/tui/themes/theme-json.js';

// 测试环境非 TTY（chalk level=0 不上色）——强制真彩色让 ansi 断言有效
let savedChalkLevel: number;
before(() => {
  savedChalkLevel = chalk.level;
  chalk.level = 3;
});
after(() => {
  chalk.level = savedChalkLevel;
});

/** 最小合法主题（46 token 全填 hex）。 */
function minimalTheme(name: string): Record<string, unknown> {
  const colorTokens = [
    'accent', 'border', 'borderAccent', 'borderMuted', 'success', 'error', 'warning',
    'muted', 'dim', 'text', 'thinkingText', 'selectedBg', 'userMessageBg',
    'userMessageText', 'customMessageBg', 'customMessageText', 'customMessageLabel',
    'toolPendingBg', 'toolSuccessBg', 'toolErrorBg', 'toolTitle', 'toolOutput',
    'toolDiffAdded', 'toolDiffRemoved', 'toolDiffContext', 'bashMode',
    'mdHeading', 'mdLink', 'mdLinkUrl', 'mdCode', 'mdCodeBlock', 'mdCodeBlockBorder',
    'mdQuote', 'mdQuoteBorder', 'mdHr', 'mdListBullet',
    'syntaxComment', 'syntaxKeyword', 'syntaxFunction', 'syntaxVariable', 'syntaxString',
    'syntaxNumber', 'syntaxType', 'syntaxOperator', 'syntaxPunctuation',
  ];
  const colors: Record<string, string> = {};
  for (const token of colorTokens) colors[token] = '#aabbcc';
  return { name, colors };
}

describe('parseThemeJson', () => {
  it('合法主题（含多余字段容忍）', () => {
    const raw = { ...minimalTheme('x'), thinkingOff: '#111111', export: { pageBg: '#000' } };
    const json = parseThemeJson('x', raw);
    assert.equal(json.name, 'x');
    assert.equal(json.colors.accent, '#aabbcc');
  });

  it('缺必需 token → 错误列出缺失清单', () => {
    const raw = minimalTheme('x') as { colors: Record<string, string> };
    delete raw.colors.accent;
    delete raw.colors.mdCode;
    assert.throws(
      () => parseThemeJson('x', raw),
      (error: Error) =>
        error.message.includes('缺必需色 token') &&
        error.message.includes('- accent') &&
        error.message.includes('- mdCode'),
    );
  });

  it('非法色值（超范围索引/非字符串数字）→ 报错', () => {
    const raw = minimalTheme('x') as { colors: Record<string, unknown> };
    raw.colors.accent = 300;
    assert.throws(() => parseThemeJson('x', raw), /非法色值/);
  });

  it('name 含 "/" 拒绝（保留给自动双主题语法位）', () => {
    assert.throws(() => parseThemeJson('x', { name: 'a/b', colors: {} }), /不能包含/);
  });

  it('非对象顶层拒绝', () => {
    assert.throws(() => parseThemeJson('x', ['a']), /顶层必须是对象/);
  });
});

describe('resolveThemeColors', () => {
  it('vars 引用解析（含链式）', () => {
    const json: ThemeJson = {
      name: 'x',
      vars: { base: '#112233', alias: 'base' },
      colors: { accent: 'alias' },
    };
    assert.equal(resolveThemeColors(json).accent, '#112233');
  });

  it('环引用报错', () => {
    const json: ThemeJson = {
      name: 'x',
      vars: { a: 'b', b: 'a' },
      colors: { accent: 'a' },
    };
    assert.throws(() => resolveThemeColors(json), /环引用/);
  });

  it('未定义引用报错', () => {
    const json: ThemeJson = { name: 'x', colors: { accent: 'nope' } };
    assert.throws(() => resolveThemeColors(json), /未定义/);
  });
});

describe('createThemeFace', () => {
  it('hex → chalk.hex 函数（fg/bg 分流）；空串 → identity；索引 → ansi256', () => {
    const face = createThemeFace(BUILTIN_DARK);
    // fg hex：accent var → #8abeb7
    assert.equal(face.colors.accent('x'), `\x1b[38;2;138;190;183mx\x1b[39m`);
    // bg hex：selectedBg → #3a3a4a
    assert.equal(face.colors.selectedBg('x'), `\x1b[48;2;58;58;74mx\x1b[49m`);
    // markdown/syntax/editor 四件套齐备
    assert.equal(typeof face.markdownTheme.heading, 'function');
    assert.equal(face.syntaxColors.syntaxComment, '#6A9955');
    assert.equal(typeof face.editorTheme.borderColor, 'function');
    // light 主题的 accent 不同（ vars 解析正确）
    const light = createThemeFace(BUILTIN_LIGHT);
    assert.notEqual(light.colors.accent('x'), face.colors.accent('x'));
  });
});

describe('主题单例与切换', () => {
  before(() => {
    initTheme('dark');
  });
  after(() => {
    setCustomThemesDirForTest(undefined);
    setTheme('dark');
  });

  it('setTheme 切换 → onThemeChange 回调 + Proxy 活引用取新色', () => {
    const before = colors.accent('x');
    let fired = 0;
    onThemeChange(() => fired++);
    assert.equal(setTheme('light').success, true);
    assert.equal(getCurrentThemeName(), 'light');
    assert.equal(fired, 1);
    // 同一个 colors Proxy：读取行为已变（light accent = teal #5a8080）
    assert.notEqual(colors.accent('x'), before);
    setTheme('dark');
  });

  it('setTheme 失败 → 回退 dark + error', () => {
    setTheme('light');
    const result = setTheme('nonexistent-theme');
    assert.equal(result.success, false);
    assert.match(result.error!, /不存在/);
    assert.equal(getCurrentThemeName(), 'dark');
    setTheme('dark');
  });

  it('getAvailableThemes：内建 dark/light + 自定义目录收录与诊断、内建优先', () => {
    const dir = mkdtempSync(join(tmpdir(), 'nova-themes-test-'));
    try {
      setCustomThemesDirForTest(dir);
      // 合法自定义主题
      writeFileSync(join(dir, 'mine.json'), JSON.stringify(minimalTheme('mine')));
      // 与内建同名（内建优先去重）
      writeFileSync(join(dir, 'dark.json'), JSON.stringify(minimalTheme('dark')));
      // 坏文件（缺 token）→ 诊断
      writeFileSync(join(dir, 'broken.json'), JSON.stringify({ name: 'broken', colors: {} }));
      // 非 json 文件忽略
      writeFileSync(join(dir, 'README.txt'), 'hello');

      const { themes, diagnostics } = getAvailableThemes();
      const names = themes.map((t) => t.name);
      assert.deepEqual(names.slice(0, 2), ['dark', 'light']);
      assert.ok(names.includes('mine'));
      assert.equal(names.filter((n) => n === 'dark').length, 1); // 内建优先
      assert.equal(diagnostics.length, 1);
      assert.match(diagnostics[0]!, /broken\.json/);
      // 自定义主题可加载切换
      assert.equal(setTheme('mine').success, true);
      setTheme('dark');
    } finally {
      setCustomThemesDirForTest(undefined);
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('initTheme 无名 → COLORFGBG 检测兜底；坏名静默回退 dark', () => {
    const saved = process.env.COLORFGBG;
    try {
      process.env.COLORFGBG = '0;15'; // 亮背景
      initTheme();
      assert.equal(getCurrentThemeName(), 'light');
      process.env.COLORFGBG = '15;0'; // 暗背景
      initTheme();
      assert.equal(getCurrentThemeName(), 'dark');
      delete process.env.COLORFGBG;
      initTheme();
      assert.equal(getCurrentThemeName(), 'dark');
      initTheme('nonexistent');
      assert.equal(getCurrentThemeName(), 'dark');
    } finally {
      if (saved === undefined) delete process.env.COLORFGBG;
      else process.env.COLORFGBG = saved;
    }
  });

  it('包注册主题（registerPackageThemes）：清单可见 + 可切换 + 全量替换', () => {
    const theme = parseThemeJson('pkg-theme', minimalTheme('pkg-theme'));
    registerPackageThemes(new Map([['pkg-theme', theme]]));
    try {
      const { themes } = getAvailableThemes();
      assert.ok(themes.some((t) => t.name === 'pkg-theme' && t.source === 'package'));
      assert.equal(setTheme('pkg-theme').success, true);
      assert.equal(getCurrentThemeName(), 'pkg-theme');
      // 全量替换语义：空表替换后主题消失
      registerPackageThemes(new Map());
      assert.equal(setTheme('pkg-theme').success, false);
    } finally {
      setTheme('dark');
    }
  });
});

describe('automatic 档（跟随终端亮暗）', () => {
  it("setTheme('automatic')：逻辑名保持 automatic，按 COLORFGBG 解析内建", () => {
    const saved = process.env.COLORFGBG;
    try {
      process.env.COLORFGBG = '0;15'; // 亮背景
      assert.equal(setTheme('automatic').success, true);
      assert.equal(getCurrentThemeName(), 'automatic');
      const lightFace = createThemeFace(BUILTIN_LIGHT);
      assert.equal(colors.accent('x'), lightFace.colors.accent('x'));

      process.env.COLORFGBG = '15;0'; // 暗背景
      setTheme('automatic');
      assert.equal(getCurrentThemeName(), 'automatic');
      const darkFace = createThemeFace(BUILTIN_DARK);
      assert.equal(colors.accent('x'), darkFace.colors.accent('x'));
    } finally {
      if (saved === undefined) delete process.env.COLORFGBG;
      else process.env.COLORFGBG = saved;
    }
  });

  it("initTheme('automatic')：启动路径同语义；export 数据取解析后的内建", () => {
    const saved = process.env.COLORFGBG;
    try {
      process.env.COLORFGBG = '0;15';
      initTheme('automatic');
      assert.equal(getCurrentThemeName(), 'automatic');
      // export 数据不因逻辑名解析不到而回退 dark（currentJson 直取）
      const light = getExportThemeData().cssColors.accent;
      process.env.COLORFGBG = '15;0';
      initTheme('automatic');
      const dark = getExportThemeData().cssColors.accent;
      assert.notEqual(light, dark);
    } finally {
      if (saved === undefined) delete process.env.COLORFGBG;
      else process.env.COLORFGBG = saved;
      initTheme('dark');
    }
  });

  it('bindTerminalThemeSync：配色通知驱动切换 + 通知开关同步', () => {
    const state = {
      notifications: undefined as boolean | undefined,
      listener: undefined as ((scheme: 'dark' | 'light') => void) | undefined,
    };
    const tui = {
      onTerminalColorSchemeChange(listener: (scheme: 'dark' | 'light') => void) {
        state.listener = listener;
        return () => {};
      },
      setTerminalColorSchemeNotifications(enabled: boolean) {
        state.notifications = enabled;
      },
    };

    let fired = 0;
    onThemeChange(() => fired++);
    setTheme('automatic');
    const unsubscribe = bindTerminalThemeSync(tui);
    assert.equal(state.notifications, true); // automatic 档开启通知

    const before = colors.accent('x');
    state.listener!('light'); // 终端切亮 → 跟随
    assert.equal(colors.accent('x') === before, false);
    assert.ok(fired >= 1);
    state.listener!('light'); // 同值去重——不重复 apply
    const firedAfterSame = fired;
    state.listener!('light');
    assert.equal(fired, firedAfterSame);

    setTheme('dark'); // 切走具体主题 → 通知关闭
    assert.equal(state.notifications, false);
    const firedAtDark = fired;
    state.listener!('light'); // 非 automatic——忽略
    assert.equal(fired, firedAtDark);

    setTheme('automatic'); // 切回 → 通知重开
    assert.equal(state.notifications, true);
    unsubscribe();
    setTheme('dark');
  });
});

describe('主题文件 watcher', () => {
  it('用户主题文件变更 → 当前主题热重载（onThemeChange 触发）', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'nova-theme-watch-'));
    try {
      setCustomThemesDirForTest(dir);
      const v1 = minimalTheme('hot') as { colors: Record<string, string> };
      v1.colors.accent = '#112233';
      writeFileSync(join(dir, 'hot.json'), JSON.stringify(v1));
      assert.equal(setTheme('hot').success, true);
      const before = colors.accent('x');

      let fired = 0;
      onThemeChange(() => fired++);
      const stop = watchThemeFiles();
      try {
        const v2 = minimalTheme('hot') as { colors: Record<string, string> };
        v2.colors.accent = '#445566';
        writeFileSync(join(dir, 'hot.json'), JSON.stringify(v2));
        // fs.watch + 100ms 去抖——共享 runner 上 watch 延迟可能超固定等待，
        // 轮询到触发为止（5s 预算）
        const deadline = Date.now() + 5000;
        while (fired < 1 && Date.now() < deadline) {
          await new Promise((resolve) => setTimeout(resolve, 50));
        }
        assert.ok(fired >= 1, '文件变化应触发 onThemeChange');
        assert.notEqual(colors.accent('x'), before);
      } finally {
        stop();
      }
    } finally {
      setCustomThemesDirForTest(undefined);
      setTheme('dark');
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('包主题文件变更 → 注册表刷新后重载（registerPackageThemePaths 登记路径）', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'nova-pkg-theme-watch-'));
    try {
      const filePath = join(dir, 'ocean.json');
      const v1 = minimalTheme('ocean') as { colors: Record<string, string> };
      v1.colors.accent = '#111111';
      writeFileSync(filePath, JSON.stringify(v1));
      registerPackageThemes(
        new Map([['ocean', parseThemeJson(filePath, JSON.parse(JSON.stringify(v1)))]]),
      );
      registerPackageThemePaths(new Map([['ocean', filePath]]));
      assert.equal(setTheme('ocean').success, true);
      const before = colors.accent('x');

      let fired = 0;
      onThemeChange(() => fired++);
      const stop = watchThemeFiles();
      try {
        const v2 = minimalTheme('ocean') as { colors: Record<string, string> };
        v2.colors.accent = '#777777';
        writeFileSync(filePath, JSON.stringify(v2));
        await new Promise((resolve) => setTimeout(resolve, 800));
        assert.ok(fired >= 1);
        assert.notEqual(colors.accent('x'), before);
      } finally {
        stop();
      }
    } finally {
      registerPackageThemes(new Map());
      registerPackageThemePaths(new Map());
      setTheme('dark');
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('内建主题无文件源——变化事件不重载（currentName 守卫）', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'nova-theme-watch-builtin-'));
    try {
      setCustomThemesDirForTest(dir);
      setTheme('dark');
      let fired = 0;
      onThemeChange(() => fired++);
      const stop = watchThemeFiles();
      try {
        writeFileSync(join(dir, 'other.json'), JSON.stringify(minimalTheme('other')));
        await new Promise((resolve) => setTimeout(resolve, 800));
        assert.equal(fired, 0); // dark 是内建——不重载
        assert.equal(getCurrentThemeName(), 'dark');
      } finally {
        stop();
      }
    } finally {
      setCustomThemesDirForTest(undefined);
      setTheme('dark');
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
