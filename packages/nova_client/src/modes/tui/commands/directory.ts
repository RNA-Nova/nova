/**
 * 命令目录（三源合并的单一事实源）。
 *
 * 用户可调用命令全集 = 后端 RPC（扩展命令 + prompt 模板 + skills）
 * + 前端本地命令（本表）+ Node 扩展 slot 命令（registerCommand——
 * 含 builtin /packages 与包自持 slash UI）。
 *
 * 此前有三份各自为政的清单：补全目录（editor 内联合并）、后端 /help
 * （只有后端 26 个）、用户对"可用命令"的心智（34 个）。本模块收拢：
 * 补全目录与 /help 视图共用同一份合并结果（按名去重，覆盖优先级 =
 * 分发现实：本地 > slot > 后端）。
 */

import type { SlashCommand } from '@earendil-works/pi-tui';

import { commandSlot, type NovaUIRuntime } from '../../../index.js';

/** 命令来源标签（/help 分组展示用）。 */
export type CommandSource = 'backend' | 'local' | 'slot';

/** 命令行为类型（command=本地动作 / prompt=提示词模板，展开后发给 LLM / skill=技能展开）。 */
export type CommandKind = 'command' | 'prompt' | 'skill';

export interface CommandDirectoryEntry {
  name: string;
  description?: string;
  source: CommandSource;
  /** 行为类型——prompt/skill 与 command 外观相同但行为迥异（发给模型 vs 本地动作），UI 须标注。 */
  kind: CommandKind;
}

/** 前端本地命令（typed 由 editor.submitText 本地段分发——不进后端命令表）。 */
export const LOCAL_COMMANDS: SlashCommand[] = [
  { name: 'help', description: '查看全部可用命令（含本地命令）' },
  { name: 'theme', description: '切换主题（移动即预览，Enter 持久化）' },
  { name: 'settings', description: '可视化编辑设置（主题/双 Esc/启动等）' },
  { name: 'copy', description: '复制最后一条回复到剪贴板' },
  { name: 'hotkeys', description: '查看全部键位（含自定义重绑定）' },
  { name: 'debug', description: '镜像状态 dump 到文件（frontend/tui/debug/debug-*.log）' },
  { name: 'share', description: '分享会话为 secret gist（需 gh CLI）' },
  { name: 'changelog', description: '查看更新日志' },
  { name: 'quit', description: '退出 nova' },
];

/**
 * 合并三源命令目录（按名去重；覆盖优先级 = 分发现实：本地 > slot > 后端）。
 *
 * @param isCommandEnabled 快照 allowedCommands/disabledCommands 过滤
 *   （被滤命令不进目录——与补全/typed 分发同判）。
 */
export async function buildCommandDirectory(
  runtime: NovaUIRuntime,
  isCommandEnabled?: (name: string) => boolean,
): Promise<CommandDirectoryEntry[]> {
  const enabled = (name: string): boolean => isCommandEnabled?.(name) ?? true;
  const byName = new Map<string, CommandDirectoryEntry>();

  // 后端 RPC（扩展命令 + prompt 模板 + skills——含名字消毒与类型标注；
  // 线上 source 字段：extension / prompt / skill）
  const result = await runtime.invoke('getCommands');
  const rpcCommands = (result as { commands?: Array<SlashCommand & { source?: string }> })
    .commands ?? [];
  for (const cmd of rpcCommands) {
    if (typeof cmd.name !== 'string' || cmd.name.length === 0) continue;
    if (!enabled(cmd.name)) continue;
    const kind: CommandKind =
      cmd.source === 'prompt' ? 'prompt' : cmd.source === 'skill' ? 'skill' : 'command';
    byName.set(cmd.name, {
      name: cmd.name,
      description: cmd.description ?? undefined,
      source: 'backend',
      kind,
    });
  }

  // 前端本地命令（/debug 本地镜像 dump 遮蔽后端同名 prompt 模板——与分发同序）
  for (const cmd of LOCAL_COMMANDS) {
    if (!enabled(cmd.name)) continue;
    byName.set(cmd.name, {
      name: cmd.name,
      description: cmd.description ?? undefined,
      source: 'local',
      kind: 'command',
    });
  }

  // Node 扩展 slot 命令（registerCommand——描述取注册时附着函数对象的真实描述）
  for (const { key } of runtime.slots.list()) {
    if (!key.startsWith('command:')) continue;
    const name = key.slice('command:'.length);
    if (!enabled(name)) continue;
    const fn = runtime.slots.resolve<string, unknown>(commandSlot(name));
    const desc = (fn as { description?: string } | undefined)?.description;
    byName.set(name, {
      name,
      description: desc ?? '扩展命令',
      source: 'slot',
      kind: 'command',
    });
  }

  return [...byName.values()];
}
