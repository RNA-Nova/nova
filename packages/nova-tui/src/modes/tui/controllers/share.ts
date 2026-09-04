/**
 * /share：分享会话为 GitHub secret gist（前端自持）。
 *
 * 流程：gh 可用性检查（未安装/未登录两条明确错误）→ 导出会话 HTML 到临时
 * 文件 → ``gh gist create --public=false``（Esc 可取消——前台任务登记处）→
 * gist URL 进 transcript。临时文件尽力清理。
 *
 * 无 viewer URL 拼接（导出物是自包含 HTML，gist 原始文件下载打开即可看）。
 */

import { spawn, spawnSync } from 'node:child_process';
import { unlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';

import type { NovaUIRuntime } from 'nova-tui';

import { writeSessionHtml } from './export.js';
import type { ForegroundTasks } from './foreground.js';
import type { TranscriptController } from './transcript.js';

export async function shareSession(
  runtime: NovaUIRuntime,
  transcript: TranscriptController,
  foregroundTasks: ForegroundTasks,
): Promise<void> {
  // 1. gh 前置检查（未安装 → spawnSync error；未登录 → 非零退出码）
  // timeout 兜底：gh 挂死不得冻结事件循环
  const auth = spawnSync('gh', ['auth', 'status'], { encoding: 'utf-8', timeout: 5000 });
  if (auth.error) {
    transcript.addError('未安装 GitHub CLI (gh)——安装：https://cli.github.com/');
    return;
  }
  if (auth.status !== 0) {
    transcript.addError("GitHub CLI 未登录——先运行 'gh auth login'");
    return;
  }

  // 2. 导出会话 HTML 到临时目录
  let tmpFile: string;
  try {
    tmpFile = await writeSessionHtml(runtime, tmpdir(), undefined);
  } catch (error) {
    transcript.addError(error);
    return;
  }

  // 3. 创建 secret gist（Esc 可取消——前台任务登记处）
  transcript.addInfo('正在创建 gist…（esc 取消）');
  const child = spawn('gh', ['gist', 'create', '--public=false', tmpFile], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stdout = '';
  let stderr = '';
  let cancelled = false;
  const unregister = foregroundTasks.register(() => {
    cancelled = true;
    child.kill();
    transcript.addInfo('已取消分享');
  });
  const cleanup = () => {
    unregister();
    // 临时文件尽力清理（失败静默）
    try {
      unlinkSync(tmpFile);
    } catch {
      // 无碍
    }
  };
  child.stdout.on('data', (chunk) => (stdout += chunk));
  child.stderr.on('data', (chunk) => (stderr += chunk));
  // spawn ENOENT 类失败 close/error 双事件齐发——报错去重（用户只见一条）
  let reported = false;
  const reportError = (message: string | Error) => {
    if (reported) return;
    reported = true;
    transcript.addError(message);
  };
  child.on('close', (code) => {
    cleanup();
    if (cancelled) return; // Esc 取消：本地已提示，静默收尾
    if (code !== 0) {
      reportError(`创建 gist 失败: ${stderr.trim() || `退出码 ${code}`}`);
      return;
    }
    const url = stdout.trim();
    if (!url.startsWith('http')) {
      reportError(`无法从 gh 输出解析 gist URL: ${url || '(空)'}`);
      return;
    }
    transcript.addInfo(`已分享（secret gist）: ${url}`);
  });
  child.on('error', (error) => {
    cleanup();
    if (cancelled) return;
    reportError(error);
  });
}
