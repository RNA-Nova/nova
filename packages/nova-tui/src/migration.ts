/**
 * 前端域迁移（前后端分治 §9——旧位状态/资产搬入 ``frontend/<host>/`` 半区）。
 *
 * 旧版把前端状态散在后端状态根下（``~/.nova/agent/ui-settings.json``、
 * ``ui-state/``、``keybindings.json``、``themes/``，项目级 ``.nova/keybindings.json``），
 * 现统一归 ``frontend/<host>/`` 半区。宿主启动（TUI ``app.ts`` 装配根）时
 * 检测旧位，存在即整体搬迁：
 *
 * - **mv 语义**：只搬不删（rename），旧位不留副本；
 * - **幂等**：新位已有内容则**不搬**（不合并不覆盖），返回诊断消息；
 * - **分治边界**：前端只管自己的域——后端散养资源目录（extensions/skills/
 *   prompts/personas）归 nova_harness 的迁移（``core/config/migration.py``），
 *   互不相碰；
 * - 项目级只迁 keybindings.json（其余 project 级前端资产是本次新增能力，
 *   无旧位）；键位表是纯声明式映射，迁移不涉 trust 裁决。
 */

import { existsSync, mkdirSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { projectFrontendDir, userAgentDir, userFrontendDir } from './paths.js';

/** 一条迁移规则：旧位 → 新位（宿主级根内相对路径）。 */
interface MigrationEntry {
  /** 旧位（相对后端状态根 / 项目 .nova）。 */
  legacy: string;
  /** 新位（相对 frontend/<host>/ 根）。 */
  current: string;
}

/** user 级迁移表（旧位相对 ~/.nova/agent，新位相对 frontend/<host>/）。 */
const USER_MIGRATIONS: MigrationEntry[] = [
  { legacy: 'ui-settings.json', current: 'settings.json' },
  { legacy: 'ui-state', current: 'state' },
  { legacy: 'keybindings.json', current: 'keybindings.json' },
  { legacy: 'themes', current: 'themes' },
];

/** project 级迁移表（旧位相对 <cwd>/.nova）。 */
const PROJECT_MIGRATIONS: MigrationEntry[] = [
  { legacy: 'keybindings.json', current: 'keybindings.json' },
];

/** 逐条执行迁移表，返回迁移/诊断消息（无旧位零副作用）。 */
function applyMigrations(
  legacyBase: string,
  frontendRoot: string,
  entries: MigrationEntry[],
): string[] {
  const messages: string[] = [];
  for (const { legacy, current } of entries) {
    const oldPath = join(legacyBase, legacy);
    if (!existsSync(oldPath)) continue;
    const newPath = join(frontendRoot, current);
    if (existsSync(newPath)) {
      messages.push(
        `前端域迁移跳过（新位已有内容，不合并不覆盖）：${oldPath} → ${newPath}——请人工合并后删除旧位`,
      );
      continue;
    }
    mkdirSync(frontendRoot, { recursive: true });
    renameSync(oldPath, newPath);
    messages.push(`前端域迁移完成：${oldPath} → ${newPath}`);
  }
  return messages;
}

/**
 * 执行 user + project 两级前端域迁移（宿主启动单一入口，必须在任何
 * settings/keybindings 读取之前调用）。返回迁移/诊断消息列表。
 */
export function migrateFrontendLayout(cwd: string, host?: string): string[] {
  return [
    ...applyMigrations(userAgentDir(), userFrontendDir(host), USER_MIGRATIONS),
    ...applyMigrations(join(cwd, '.nova'), projectFrontendDir(cwd, host), PROJECT_MIGRATIONS),
  ];
}
