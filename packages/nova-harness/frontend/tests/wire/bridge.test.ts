import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import type { ReverseFrame } from '../../src/wire/client.js';
import { ReverseBridge, type ReverseChannel } from '../../src/wire/bridge.js';

/** 假反向通道：录下 send 的帧，允许测试手动注入反向帧。 */
function makeChannel() {
  const sent: Record<string, unknown>[] = [];
  let sink: ((frame: ReverseFrame) => void) | undefined;
  const channel: ReverseChannel = {
    onReverse(cb) {
      sink = cb;
    },
    send(frame) {
      sent.push(frame);
    },
  };
  return {
    channel,
    sent,
    push: (frame: ReverseFrame) => sink?.(frame),
  };
}

describe('ReverseBridge 路由', () => {
  it('ui/request：线上 {id, component:{componentType, ...}} → 词汇 {id, component, params}', () => {
    const { channel, push } = makeChannel();
    const bridge = new ReverseBridge(channel);
    const seen: unknown[] = [];
    bridge.onRequest((req) => seen.push(req));

    push({
      id: '42',
      method: 'ui/request',
      params: {
        id: '42',
        component: { componentType: 'confirm', title: '信任此项目？', message: '……' },
      },
    });

    assert.deepEqual(seen, [
      { id: '42', component: 'confirm', params: { title: '信任此项目？', message: '……' } },
    ]);
  });

  it('ui/notify：线上 {method, ...payload} → 词汇 {name, params}', () => {
    const { channel, push } = makeChannel();
    const bridge = new ReverseBridge(channel);
    const seen: unknown[] = [];
    bridge.onNotice((notice) => seen.push(notice));

    push({
      method: 'ui/notify',
      params: { method: 'package_progress', message: '安装中', percent: 60 },
    });

    assert.deepEqual(seen, [
      { name: 'package_progress', params: { message: '安装中', percent: 60 } },
    ]);
  });

  it('无 id 的 ui/request 被丢弃（无法应答的请求无意义）', () => {
    const { channel, push } = makeChannel();
    const bridge = new ReverseBridge(channel);
    const seen: unknown[] = [];
    bridge.onRequest((req) => seen.push(req));
    push({ method: 'ui/request', params: { component: { componentType: 'confirm' } } });
    assert.equal(seen.length, 0);
  });

  it('未知反向方法静默忽略（向前兼容：M4 tool/invoke 落地前）', () => {
    const { channel, push } = makeChannel();
    const bridge = new ReverseBridge(channel);
    const seen: unknown[] = [];
    bridge.onRequest((req) => seen.push(req));
    bridge.onNotice((n) => seen.push(n));
    push({ id: 't1', method: 'tool/invoke', params: { id: 't1', name: 'x' } });
    assert.equal(seen.length, 0);
  });

  it('无 handler 的 ui/request 自动应答 cancelled（NoOp 等价物，不挂后端 300s）', () => {
    const { channel, sent, push } = makeChannel();
    // 故意不注册 onRequest
    new ReverseBridge(channel);

    push({
      id: '42',
      method: 'ui/request',
      params: { id: '42', component: { componentType: 'confirm', title: 't', message: 'm' } },
    });

    assert.deepEqual(sent, [
      {
        jsonrpc: '2.0',
        id: 'ui-resp-42',
        method: 'ui/response',
        params: { id: '42', result: { cancelled: true } },
      },
    ]);
  });

  it('respond 写出正确形状的 ui/response 帧', () => {
    const { channel, sent } = makeChannel();
    const bridge = new ReverseBridge(channel);
    bridge.respond('42', { confirmed: true });
    assert.deepEqual(sent, [
      {
        jsonrpc: '2.0',
        id: 'ui-resp-42',
        method: 'ui/response',
        params: { id: '42', result: { confirmed: true } },
      },
    ]);
  });

  it('sendCapabilities 写出 system/capabilities 帧', () => {
    const { channel, sent } = makeChannel();
    const bridge = new ReverseBridge(channel);
    bridge.sendCapabilities(['select', 'confirm']);
    assert.equal(sent.length, 1);
    assert.equal(sent[0]?.method, 'system/capabilities');
    assert.deepEqual(sent[0]?.params, { capabilities: ['select', 'confirm'] });
  });
});
