/**
 * 端到端冒烟（一次性脚本——真后端 + 真 LLM + NovaUIRuntime 直连）：
 * camelCase 大迁移后的全链路真实验证。
 *
 * 验证点：
 * 1. 后端启动 + 握手 + createSession（camel 参数）；
 * 2. 真实 prompt → 事件流（线上帧 camel 形状抽查）→ turn 结束；
 * 3. getSessionState 快照 camel 字段；
 * 4. getSessionEntries 条目 camel 形状（template.js 同构性的线上证据）；
 * 5. 工具调用（模型自主决定——提示词引导 read 工具）。
 *
 * 运行：VOLCENGINE_API_KEY=... npx tsx scripts/smoke-e2e.ts
 */

import { NovaUIRuntime } from '../src/index.js';

const apiKey = process.env.VOLCENGINE_API_KEY;
if (!apiKey) {
  console.error('需要 VOLCENGINE_API_KEY');
  process.exit(1);
}

const python = process.env.NOVA_PYTHON ?? 'python3';
const runtime = new NovaUIRuntime({
  command: [python, '-m', 'nova_harness.modes.rpc.cli'],
  capabilities: ['select', 'confirm', 'input', 'notify'],
  session: { cwd: '/tmp/nova-smoke', model: 'volcengine/deepseek-v3-2-251201' },
});

let failed = 0;
const check = (name: string, ok: boolean, detail = '') => {
  console.log(`${ok ? '✔' : '✖'} ${name}${detail ? ` — ${detail}` : ''}`);
  if (!ok) failed++;
};

// 线上帧形状抽查（camel 证据）
const snakeFieldSeen: string[] = [];
runtime.bus.on('*', (event) => {
  const data = (event as { data?: unknown }).data;
  if (typeof data !== 'object' || data === null) return;
  for (const key of Object.keys(data)) {
    if (key.includes('_')) snakeFieldSeen.push(`${event.type}.${key}`);
  }
});

console.log('— 启动后端…');
await runtime.start();
check('后端启动 + 握手 + createSession', true);

const snapshot = await runtime.invoke('getSessionState');
const snap = snapshot as Record<string, unknown>;
check('快照 camel 字段（sessionId）', typeof snap.sessionId === 'string', String(snap.sessionId));
check('快照 camel 字段（thinkingLevel）', typeof snap.thinkingLevel === 'string');
check('快照无 snake 字段', !Object.keys(snap).some((k) => k.includes('_')));

console.log('— 发送 prompt（引导一次 read 工具调用）…');
const start = Date.now();
await runtime.prompt('请用 read 工具读取 /tmp/nova-smoke-readme.txt 的内容，然后一句话告诉我它说了什么。', undefined);
// 等 turn 结束（agent_end）
await new Promise<void>((resolve) => {
  const off = runtime.bus.on('agent_end', () => {
    off();
    resolve();
  });
  setTimeout(resolve, 60_000);
});
console.log(`— turn 结束（${((Date.now() - start) / 1000).toFixed(1)}s）`);

check('事件流无 snake 字段', snakeFieldSeen.length === 0, snakeFieldSeen.slice(0, 3).join(','));

const entriesResult = await runtime.invoke('getSessionEntries');
const entries = (entriesResult as { entries?: unknown[] }).entries ?? [];
const first = entries[0] as Record<string, unknown> | undefined;
check('条目数量 > 0', entries.length > 0, `${entries.length} 条`);
check(
  '条目 camel 形状（parentId 字段存在性形态）',
  first !== undefined && 'parentId' in first && !('parent_id' in first),
);

const stats = await runtime.invoke('getSessionStats');
const tokens = (stats as Record<string, unknown>).tokens as Record<string, unknown> | undefined;
check('stats camel（inputTokens）', tokens !== undefined && 'inputTokens' in tokens);

await runtime.stop();
console.log(failed === 0 ? '\n全部通过 ✓' : `\n${failed} 项失败 ✗`);
process.exit(failed === 0 ? 0 : 1);
