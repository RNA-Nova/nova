/**
 * 工具卡片图片内联渲染测试（buildImageComponents 纯函数面）：
 * 协议不支持 → ``[图片: <mimeType>]`` 文本回退；支持 → Spacer+Image 组件；
 * 数据不全的块跳过。
 */

import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';

import {
  Image,
  Spacer,
  Text,
  resetCapabilitiesCache,
  setCapabilities,
  type TerminalCapabilities,
} from '@earendil-works/pi-tui';

import { buildImageComponents } from '../../../../../src/modes/tui/components/transcript/tool-execution.js';

const PNG =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';

function caps(images: TerminalCapabilities['images']): TerminalCapabilities {
  return { images, trueColor: true, hyperlinks: true };
}

afterEach(() => {
  resetCapabilitiesCache(); // 还原真实探测（防泄漏到其他用例）
});

describe('buildImageComponents', () => {
  it('无图片块 → 空表', () => {
    assert.deepEqual(buildImageComponents(undefined), []);
    assert.deepEqual(buildImageComponents([{ type: 'text', text: 'x' }]), []);
  });

  it('data/mimeType 不全的块跳过', () => {
    assert.deepEqual(buildImageComponents([{ type: 'image' }]), []);
    assert.deepEqual(buildImageComponents([{ type: 'image', data: PNG }]), []);
    assert.deepEqual(buildImageComponents([{ type: 'image', data: '', mimeType: 'image/png' }]), []);
  });

  it('终端不支持图片协议 → [图片: <mimeType>] 文本回退（一行一张）', () => {
    setCapabilities(caps(null));
    const components = buildImageComponents([
      { type: 'image', data: PNG, mimeType: 'image/png' },
      { type: 'image', data: PNG, mimeType: 'image/jpeg' },
    ]);
    assert.equal(components.length, 1);
    const text = components[0]!;
    assert.ok(text instanceof Text);
    // Text 带 paddingX=1 与行宽填充——按 trim 后内容断言
    assert.deepEqual(
      text.render(80).map((line) => line.trim()),
      ['[图片: image/png]', '[图片: image/jpeg]'],
    );
  });

  it('终端支持图片协议 → 每张 Spacer + Image 组件', () => {
    setCapabilities(caps('iterm2'));
    const components = buildImageComponents([
      { type: 'image', data: PNG, mimeType: 'image/png' },
    ]);
    assert.equal(components.length, 2);
    assert.ok(components[0] instanceof Spacer);
    assert.ok(components[1] instanceof Image);
  });

  it('kitty 协议同样走 Image 组件（非 PNG 由组件内 fallback 兜底）', () => {
    setCapabilities(caps('kitty'));
    const components = buildImageComponents([
      { type: 'image', data: PNG, mimeType: 'image/png' },
    ]);
    assert.equal(components.length, 2);
    assert.ok(components[1] instanceof Image);
  });
});
