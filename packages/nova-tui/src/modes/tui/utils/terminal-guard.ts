/**
 * 终端守卫的 tmux 检测部分：
 *
 * - **tmux 键位检测**：extended-keys 未开 / format 为 xterm 时给出
 *   配置指引警告（shift+enter 等修饰键在 tmux 默认配置下不可达）；
 *   查询失败（超时/沙箱）不警告；
 * - **tmux 版本检测**（``checkTmuxExtendedKeys``）：tmux < 3.1 无
 *   extended-keys 能力（选项查询在旧版上查不到、不会触发上面的警告）——
 *   单独按 ``tmux -V`` 版本号告警升级。
 *
 * 死终端应急退出/崩溃恢复/信号优雅退出归 ``utils/signals.ts``
 * （installSignalHandlers——能力与幂等更全的后继者）；死终端判定
 * （``isDeadTerminalError``）由 signals 复用本模块。
 */

import { spawn, spawnSync } from 'node:child_process';

const DEAD_TERMINAL_CODES = new Set(['EIO', 'EPIPE', 'ENOTCONN']);

function isDeadTerminalError(error: unknown): boolean {
  if (typeof error !== 'object' || error === null || !('code' in error)) return false;
  const code = (error as { code?: unknown }).code;
  return typeof code === 'string' && DEAD_TERMINAL_CODES.has(code);
}

/** 导出供 signals 与测试（判定逻辑独立验证）。 */
export { isDeadTerminalError };

/** tmux 键位配置检测；无问题/查不到返回 null。 */
export async function checkTmuxKeyboardSetup(): Promise<string | null> {
  if (!process.env.TMUX) return null;

  const query = (option: string): Promise<string | undefined> =>
    new Promise((resolve) => {
      const proc = spawn('tmux', ['show', '-gv', option], {
        stdio: ['ignore', 'pipe', 'ignore'],
      });
      let stdout = '';
      const timer = setTimeout(() => {
        proc.kill();
        resolve(undefined);
      }, 2000);
      proc.stdout?.on('data', (data) => {
        stdout += data.toString();
      });
      proc.on('error', () => {
        clearTimeout(timer);
        resolve(undefined);
      });
      proc.on('close', (code) => {
        clearTimeout(timer);
        resolve(code === 0 ? stdout.trim() : undefined);
      });
    });

  const [extendedKeys, format] = await Promise.all([
    query('extended-keys'),
    query('extended-keys-format'),
  ]);
  if (extendedKeys === undefined) return null; // 查询失败不警告
  if (extendedKeys !== 'on' && extendedKeys !== 'always') {
    return 'tmux extended-keys 未开启——修饰键（shift+enter 等）可能不可用。在 ~/.tmux.conf 加 `set -g extended-keys on` 并重启 tmux。';
  }
  if (format === 'xterm') {
    return 'tmux extended-keys-format 为 xterm——建议 csi-u。在 ~/.tmux.conf 加 `set -g extended-keys-format csi-u` 并重启 tmux。';
  }
  return null;
}

/** tmux 版本号解析（"tmux 3.4" / "tmux next-3.4" → [3, 4]；解析不出 null）。 */
export function parseTmuxVersion(output: string): [number, number] | null {
  const match = /tmux\s+(?:next-)?(\d+)\.(\d+)/.exec(output.trim());
  if (!match) return null;
  return [Number.parseInt(match[1]!, 10), Number.parseInt(match[2]!, 10)];
}

/**
 * tmux extended-keys 能力检查（版本维度）：tmux < 3.1 无 extended-keys
 * 支持（选项查询在旧版返回空——checkTmuxKeyboardSetup 覆盖不到）。
 * 返回警告文案；非 tmux 会话 / 查询失败 / 版本足够返回 null。
 * ``probeVersion`` 可注入（测试——默认 ``tmux -V`` 子进程探测）。
 */
export function checkTmuxExtendedKeys(
  probeVersion: () => [number, number] | null = probeTmuxVersion,
): string | null {
  if (!process.env.TMUX) return null;
  const version = probeVersion();
  if (version === null) return null; // 查不到不警告
  const [major, minor] = version;
  if (major > 3 || (major === 3 && minor >= 1)) return null;
  return `tmux ${major}.${minor} 过旧（< 3.1），不支持 extended-keys——修饰键（shift+enter 等）可能不可用。升级 tmux 至 3.1+ 并在 ~/.tmux.conf 加 \`set -g extended-keys on\`。`;
}

/** 默认版本探针：tmux -V（超时/异常/解析失败归 null）。 */
function probeTmuxVersion(): [number, number] | null {
  const result = spawnSync('tmux', ['-V'], {
    encoding: 'utf-8',
    timeout: 2000,
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  if (result.error !== undefined || result.status !== 0) return null;
  return parseTmuxVersion(result.stdout ?? '');
}
