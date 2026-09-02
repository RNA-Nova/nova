/**
 * npm 自愈（packages/ 子系统）。
 *
 * nova-pkg 主装（install 第 ⑤ 阶段），本层兜底——加载前发现包根有
 * package.json 但 node_modules 缺失（离线安装/当时 npm 不在/手动复制），
 * 补跑一次 ``npm install --omit=dev``。两个入口一个真相：目录现状。
 */

import { spawn } from 'node:child_process';

/**
 * 在包根执行 ``npm install --omit=dev``。
 * 返回是否成功；npm 缺失/网络失败均返回 false（调用方降级，不阻断）。
 */
export function ensureNpmDependencies(installPath: string): Promise<boolean> {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn('npm', ['install', '--omit=dev'], {
        cwd: installPath,
        stdio: 'ignore',
      });
    } catch {
      resolve(false);
      return;
    }
    child.on('error', () => resolve(false));
    child.on('exit', (code) => resolve(code === 0));
  });
}
