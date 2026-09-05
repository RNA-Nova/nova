/**
 * 后端启动命令解析（打包形态的后端发现）。
 *
 * 解析链（从高到低）：
 * 1. `NOVA_BACKEND`：显式指定后端二进制路径（调试/非常规布局）；
 * 2. 同目录 `runtime/nova-server[.exe]`：打包形态（二进制旁的随行后端）；
 * 3. `NOVA_PYTHON`：开发态显式指定后端解释器；
 * 4. `python3 -m nova_harness.modes.rpc.cli`：开发态默认（pip 渠道）。
 */

import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';

export function resolveBackendCommand(
  env: NodeJS.ProcessEnv = process.env,
  execDir: string = dirname(process.execPath),
  platform: NodeJS.Platform = process.platform,
): string[] {
  if (env.NOVA_BACKEND) {
    return [env.NOVA_BACKEND];
  }
  const sibling = join(
    execDir,
    'runtime',
    platform === 'win32' ? 'nova-server.exe' : 'nova-server',
  );
  if (existsSync(sibling)) {
    return [sibling];
  }
  const python = env.NOVA_PYTHON ?? 'python3';
  return [python, '-m', 'nova_harness.modes.rpc.cli'];
}
