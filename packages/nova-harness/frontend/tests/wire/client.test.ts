import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { WireClient, type ReverseFrame } from '../../src/wire/client.js';

/**
 * 以 cat 为后端替身：stdin 写入的帧原样回显到 stdout。
 * 回显帧带 method → 按反向帧路由进 reverseSink，借此观察 client 写出了什么。
 */
async function startEchoClient(): Promise<{
  client: WireClient;
  reversed: ReverseFrame[];
}> {
  const client = new WireClient({ command: ['cat'], forwardStderr: false });
  const reversed: ReverseFrame[] = [];
  client.onReverse((frame) => reversed.push(frame));
  await client.start(50);
  return { client, reversed };
}

/** 轮询等条件成立（回显帧经 readline 异步到达）。 */
async function waitFor(cond: () => boolean, timeoutMs = 2000): Promise<void> {
  const start = Date.now();
  while (!cond()) {
    if (Date.now() - start > timeoutMs) throw new Error('waitFor 超时');
    await new Promise((r) => setTimeout(r, 10));
  }
}

describe('WireClient.callCancellable', () => {
  it('cancel → 本地 AbortError reject + cancelRequest 帧发出（携带原调用 id）', async () => {
    const { client, reversed } = await startEchoClient();

    const { promise, cancel } = client.callCancellable('getSessionState', {});
    cancel();

    await assert.rejects(promise, (error: Error) => error.name === 'AbortError');

    await waitFor(() => reversed.some((f) => f.method === 'cancelRequest'));
    const frame = reversed.find((f) => f.method === 'cancelRequest');
    assert.equal(typeof frame?.params?.id, 'number');

    await client.stop();
  });

  it('重复 cancel 幂等：只发出一帧 cancelRequest', async () => {
    const { client, reversed } = await startEchoClient();

    const { promise, cancel } = client.callCancellable('getSessionState', {});
    cancel();
    cancel(); // pending 已删：静默空转
    await assert.rejects(promise, (error: Error) => error.name === 'AbortError');

    await waitFor(() => reversed.some((f) => f.method === 'cancelRequest'));
    await new Promise((r) => setTimeout(r, 100)); // 给可能误发的第二帧留到达窗口
    assert.equal(
      reversed.filter((f) => f.method === 'cancelRequest').length,
      1,
    );

    await client.stop();
  });
});
