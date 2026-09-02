/** RPC 全链探针：真后端 + 真 wire 客户端，prompt("/import") 的事件流。 */
import { NovaUIRuntime } from '../src/index.js';

const python = process.env.NOVA_PYTHON ?? 'python3';
const runtime = new NovaUIRuntime({
  command: [python, '-m', 'nova_harness.modes.rpc.cli'],
  capabilities: ['notify'],
  session: { cwd: '/tmp' },
});

const seen: string[] = [];
runtime.bus.on('*', (event) => {
  seen.push(`${event.type}: ${JSON.stringify(event).slice(0, 400)}`);
});

await runtime.start();
await runtime.prompt('/import', undefined);
await new Promise((r) => setTimeout(r, 1500));
console.log('事件流:', seen);
console.log('store entries:', JSON.stringify(runtime.store.transcript?.entries ?? []).slice(0, 300));
await runtime.shutdown?.();
process.exit(0);
