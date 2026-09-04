/**
 * keybindings.json 加载与三级合并。
 *
 * 三级来源（优先级 project > user > builtin；前后端分治 §9——键位归前端域）：
 * - builtin：宿主注入的默认表各 actionId 的 defaultKeys（本层不持有任何
 *   具体表——机械归运行时，表归宿主）；
 * - user：``~/.nova/agent/frontend/tui/keybindings.json``；
 * - project：``<cwd>/.nova/frontend/tui/keybindings.json``。
 *
 * 文件格式：``{ "<actionId>": "ctrl+x" | ["ctrl+x", "f2"] }``——
 * 按 actionId **整体替换**默认键（不是追加）；空数组 = 禁用该动作。
 *
 * 合并发生在 user/project 两层（同名 actionId project 覆盖 user），合成
 * 一份 effective userBindings 交给 pi-tui KeybindingsManager（它对
 * builtin 做最终替换）。未知 actionId / 非法值跳过并产生诊断（拼写错误
 * 可被发现，而非静默无效）。
 *
 * 项目级不做 trust 门控：键位表是纯声明式映射，无代码执行面。
 */

import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { KeyId, KeybindingsConfig } from '@earendil-works/pi-tui';

import { projectFrontendDir, userFrontendDir } from '../paths.js';

export interface KeybindingsLoadResult {
  config: KeybindingsConfig;
  diagnostics: string[];
}

/** 用户级键位文件路径（前端域 frontend/tui/ 半区）。 */
export function userKeybindingsPath(): string {
  return join(userFrontendDir(), 'keybindings.json');
}

/** 项目级键位文件路径（项目 .nova 下的前端域半区）。 */
export function projectKeybindingsPath(cwd: string): string {
  return join(projectFrontendDir(cwd), 'keybindings.json');
}

/** 加载单个 keybindings.json（不存在/坏 JSON/非法条目均为诊断，不抛）。
 *
 * ``knownDefinitions``：已知动作定义表（宿主注入——actionId 校验的词汇
 * 来源；未知动作跳过 + 诊断，拼写错误可被发现）。 */
export function loadKeybindingsFile(
  path: string,
  knownDefinitions: Readonly<Record<string, unknown>>,
): KeybindingsLoadResult {
  const diagnostics: string[] = [];
  if (!existsSync(path)) return { config: {}, diagnostics };

  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(path, 'utf-8'));
  } catch (error) {
    diagnostics.push(`${path}: JSON 解析失败（${(error as Error).message}）`);
    return { config: {}, diagnostics };
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    diagnostics.push(`${path}: 顶层必须是对象（actionId → 键位）`);
    return { config: {}, diagnostics };
  }

  const config: KeybindingsConfig = {};
  for (const [actionId, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!(actionId in knownDefinitions)) {
      diagnostics.push(`${path}: 未知键位动作 "${actionId}"（已跳过）`);
      continue;
    }
    if (typeof value === 'string') {
      config[actionId] = value as KeyId;
      continue;
    }
    if (Array.isArray(value) && value.every((entry) => typeof entry === 'string')) {
      config[actionId] = value as KeyId[];
      continue;
    }
    diagnostics.push(`${path}: "${actionId}" 的值必须是键位字符串或字符串数组（已跳过）`);
  }
  return { config, diagnostics };
}

/** 合并多份配置（后者同名 actionId 覆盖前者）。 */
export function mergeKeybindingsConfigs(
  ...configs: KeybindingsConfig[]
): KeybindingsConfig {
  return Object.assign({}, ...configs);
}
