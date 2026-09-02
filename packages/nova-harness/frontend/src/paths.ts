/**
 * 前端域路径族（前后端分治 §9——唯一出处）。
 *
 * 终态结构：后端状态根（``~/.nova/agent`` 的 settings/auth/sessions/packages 等）
 * 不动；**前端域**按宿主分级挂在其下的 ``frontend/<host>/`` 半区：
 *
 * ```
 * ~/.nova/agent/frontend/tui/     # user 级（本宿主）
 * ├── settings.json               # UISettings（扩展设置键）
 * ├── state/                      # UIStateStore（扩展内部 KV）
 * ├── keybindings.json            # 用户键位表
 * ├── themes/                     # 自定义主题
 * ├── debug/                      # /debug 镜像 dump
 * └── tools/ dialogs/ index.ts    # 散养渲染器/对话框/扩展入口（discovery 扫描）
 * <cwd>/.nova/frontend/tui/       # project 级同构（trust 门控）
 * ```
 *
 * 本模块只定义路径，不做任何 IO；旧位 → 新位的迁移归 ``migration.ts``。
 * 多宿主（web 等）落地时经 host 参数分段，调用点不收矢量硬编码。
 */

import { homedir } from 'node:os';
import { join } from 'node:path';

/** 当前生效的宿主段名（web 宿主落地时再参数化上收）。 */
export const FRONTEND_HOST = 'tui';

/** 后端状态根（~/.nova/agent——前端域挂在其下的 frontend/ 半区）。 */
export function userAgentDir(): string {
  return join(homedir(), '.nova', 'agent');
}

/** user 级前端域根：``~/.nova/agent/frontend/<host>/``。 */
export function userFrontendDir(host: string = FRONTEND_HOST): string {
  return join(userAgentDir(), 'frontend', host);
}

/** project 级前端域根：``<cwd>/.nova/frontend/<host>/``。 */
export function projectFrontendDir(cwd: string, host: string = FRONTEND_HOST): string {
  return join(cwd, '.nova', 'frontend', host);
}
