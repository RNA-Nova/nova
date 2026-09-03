/**
 * export/index 测试：模板装配完整性（占位符/数据内联/主题注入）。
 * 数据零映射——线上 camelCase 条目直接进 sessionData。
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { assembleHtml } from '../../src/export/index.js';

describe('assembleHtml', () => {
  const theme = {
    cssColors: { accent: '#8abeb7', text: '#d4d4d4' },
    pageBg: '#18181e',
    cardBg: '#1e1e24',
    infoBg: '#3c3728',
  };

  it('装配：占位符零残留 + 数据可解回 + 主题变量注入', () => {
    const html = assembleHtml({
      sessionData: {
        header: { id: 's1', cwd: '/tmp', timestamp: 1 },
        entries: [
          {
            id: 'e1',
            parentId: null,
            type: 'message',
            timestamp: '2026-01-01',
            message: { role: 'user', content: '你好', timestamp: 1 },
          },
        ],
        leafId: 'e1',
        renderedTools: {},
      },
      theme,
    });
    // 模板占位符零残留（不断言 '{{' 子串——template.js 的 JS 语法自带）
    for (const placeholder of [
      '{{CSS}}',
      '{{JS}}',
      '{{SESSION_DATA}}',
      '{{MARKED_JS}}',
      '{{HIGHLIGHT_JS}}',
      '{{THEME_VARS}}',
      '{{BODY_BG}}',
      '{{CONTAINER_BG}}',
      '{{INFO_BG}}',
    ]) {
      assert.equal(html.includes(placeholder), false, placeholder);
    }
    // 主题变量注入
    assert.match(html, /--accent: #8abeb7/);
    assert.match(html, /--exportPageBg: #18181e/);
    // 数据 base64 可解回
    const base64Match = html.match(/id="session-data"[^>]*>([A-Za-z0-9+/=]+)</);
    assert.ok(base64Match);
    const decoded = JSON.parse(Buffer.from(base64Match![1]!, 'base64').toString('utf-8'));
    assert.equal(decoded.leafId, 'e1');
    assert.equal(decoded.entries[0].message.role, 'user');
  });
});
