/**
 * npm 自愈（packages/ 子系统）。
 *
 * nova-pkg 主装（install 的 npm 阶段），本层兜底——加载前发现包根有
 * package.json 但 node_modules 缺失（离线安装/当时 npm 不在/手动复制），
 * 补跑一次安装。
 *
 * 纪律：
 * - **后台任务**，不归加载路径等待——调用方拿 Promise，自行安排完成后的
 *   刷新/通知；本轮缺依赖的渲染器按诊断降级，补装完成 refresh 后上线。
 * - 时长归 npm 自己的 fetch 超时/重试管，本层不设强制超时（防误杀大包）。
 * - 同目录 in-flight 去重（并发触发同一包只跑一次）。
 * - 有 package-lock.json 走 ``npm ci``（可复现），否则 ``npm install --omit=dev``。
 * - ``NOVA_OFFLINE`` 直接跳过（返回 false）；npm 缺失/失败返回 false，不抛。
 */

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

/** 同目录 in-flight 任务表（并发触发的去重闸）。 */
const inflight = new Map<string, Promise<boolean>>();

/**
 * 在包根补装 npm 依赖（后台任务）。
 * 返回是否成功；npm 缺失/网络失败/离线均返回 false（调用方降级，不阻断）。
 */
export function healNpmDependencies(installPath: string): Promise<boolean> {
  if (process.env.NOVA_OFFLINE) {
    return Promise.resolve(false);
  }
  const existing = inflight.get(installPath);
  if (existing) {
    return existing;
  }
  // 有 lockfile 用 npm ci（可复现安装），无则 npm install
  const args = existsSync(join(installPath, 'package-lock.json'))
    ? ['ci', '--omit=dev']
    : ['install', '--omit=dev'];
  const task = new Promise<boolean>((resolve) => {
    let child;
    try {
      child = spawn('npm', args, { cwd: installPath, stdio: 'ignore' });
    } catch {
      resolve(false);
      return;
    }
    child.on('error', () => resolve(false));
    child.on('exit', (code) => resolve(code === 0));
  }).finally(() => {
    inflight.delete(installPath);
  });
  inflight.set(installPath, task);
  return task;
}

/** 兼容别名（旧调用点）。 */
export const ensureNpmDependencies = healNpmDependencies;
