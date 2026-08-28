/**
 * 本机打开 URL（`host:openUrl` 宿主原语的 TUI 实现——执行位置永远在客户端）。
 *
 * 尽力而为：spawn 失败静默（授权等待框里有 URL——显示是主通道不是兜底）。
 */

import { spawn } from 'node:child_process';

export function openUrl(url: string): void {
  const platform = process.platform;
  const command = platform === 'darwin' ? 'open' : platform === 'win32' ? 'cmd' : 'xdg-open';
  const args = platform === 'win32' ? ['/c', 'start', '', url] : [url];
  const child = spawn(command, args, { detached: true, stdio: 'ignore' });
  child.on('error', () => {});
  child.unref();
}
