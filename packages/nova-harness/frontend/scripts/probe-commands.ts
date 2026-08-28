/** 补全目录三源合并结果探针（真后端，/tmp cwd，app 全选项）。 */
import { NovaUIRuntime, commandSlot, type SlashCommand } from '../src/index.js';
import { registerBuiltinBlocks } from '../src/modes/tui/blocks/index.js';
import { registerPackagePanel } from '../src/modes/tui/builtin/package-panel.js';

const python = process.env.NOVA_PYTHON ?? 'python3';
const runtime = new NovaUIRuntime({
  command: [python, '-m', 'nova_harness.modes.rpc.cli'],
  capabilities: ['select', 'confirm', 'input', 'notify', 'form', 'set_status'],
  session: { cwd: '/tmp' },
  slotsBootstrap: (api) => {
    registerBuiltinBlocks(api);
    registerPackagePanel(api);
  },
} as never);

await runtime.start();
await runtime.refreshPackages();

const result = (await runtime.invoke('getCommands')) as {
  commands: Array<{ name: string; description?: string }>;
};
const byName = new Map<string, { name: string; source: string }>();
for (const cmd of result.commands) byName.set(cmd.name, { name: cmd.name, source: 'backend' });
const locals = ['theme', 'settings', 'copy', 'hotkeys', 'debug', 'share', 'changelog', 'quit'];
for (const name of locals) byName.set(name, { name, source: 'local' });
for (const { key } of runtime.slots.list()) {
  if (!key.startsWith('command:')) continue;
  byName.set(key.slice(8), { name: key.slice(8), source: 'slot' });
}

const names = [...byName.keys()].sort();
console.log('合并后命令数:', names.length);
const counts = new Map<string, number>();
for (const n of names) counts.set(n, (counts.get(n) ?? 0) + 1);
for (const n of names) console.log(' ', n, `(${byName.get(n)!.source})`);
await runtime.shutdown?.();
process.exit(0);
