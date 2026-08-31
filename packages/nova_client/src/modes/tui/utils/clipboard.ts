/**
 * 剪贴板工具：Ctrl+V 图片/文本读取（对齐 pi handleClipboardPaste 语义）。
 *
 * 模型（对齐 pi）：剪贴板图片不落 ImageContent——写临时文件、路径以文本
 * 进编辑器，随 prompt 纯文本上送，LLM 经 read 工具读图（read 内建格式
 * 归一 / EXIF 校正 / 预算压缩）。
 *
 * 平台支持：
 * - macOS：osascript（图片，PNGf → JPEG 顺序尝试）+ pbpaste（文本）——
 *   系统内建命令，零依赖；
 * - Linux：wl-paste（Wayland）/ xclip（X11 兜底）；
 * - Windows：未实现（WSL PowerShell 路线归后续里程碑，需要时补）。
 *
 * 读取失败一律静默返回 null（无剪贴板权限等场景不打断输入流）。
 */

import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const LIST_TIMEOUT_MS = 1000;
const READ_TIMEOUT_MS = 3000;
const MAX_BUFFER_BYTES = 50 * 1024 * 1024;

/** 支持的图片 mime → 扩展名（顺序即优先级）。 */
const IMAGE_MIME_EXT: ReadonlyArray<readonly [string, string]> = [
  ['image/png', 'png'],
  ['image/jpeg', 'jpg'],
  ['image/webp', 'webp'],
  ['image/gif', 'gif'],
];

function run(
  command: string,
  args: string[],
  timeoutMs: number,
): { ok: boolean; stdout: Buffer } {
  const result = spawnSync(command, args, {
    timeout: timeoutMs,
    maxBuffer: MAX_BUFFER_BYTES,
  });
  if (result.error || result.status !== 0) {
    return { ok: false, stdout: Buffer.alloc(0) };
  }
  const stdout = Buffer.isBuffer(result.stdout)
    ? result.stdout
    : Buffer.from(result.stdout ?? '', 'utf-8');
  return { ok: true, stdout };
}

/** 从候选 mime 列表选出最优图片类型（对齐 pi selectPreferredImageMimeType）。 */
function selectImageMimeType(types: string[]): [string, string] | null {
  const bases = types.map((t) => t.split(';')[0]?.trim().toLowerCase() ?? '');
  for (const [mime, ext] of IMAGE_MIME_EXT) {
    if (bases.includes(mime)) return [mime, ext];
  }
  return null;
}

// ---------------------------------------------------------------------------
// macOS：osascript（系统内建，无需 native 绑定）
// ---------------------------------------------------------------------------

/**
 * osascript 落盘脚本：按 PNGf → JPEG 顺序尝试，写 ``<base>.<ext>`` 并
 * 返回完整路径；剪贴板无图片返回空串。路径经 argv 传入，避免字符串拼接转义。
 */
const MACOS_SAVE_SCRIPT = `
on run argv
  set basePath to item 1 of argv
  try
    set imgData to the clipboard as «class PNGf»
    set ext to "png"
  on error
    try
      set imgData to the clipboard as «class JPEG»
      set ext to "jpg"
    on error
      return ""
    end try
  end try
  set outPath to POSIX file (basePath & "." & ext)
  set fd to open for access outPath with write permission
  set eof of fd to 0
  write imgData to fd
  close access fd
  return basePath & "." & ext
end run
`;

function saveClipboardImageMacOS(basePath: string): string | null {
  const result = run('osascript', ['-e', MACOS_SAVE_SCRIPT, basePath], READ_TIMEOUT_MS);
  if (!result.ok) return null;
  const out = result.stdout.toString('utf-8').trim();
  return out.length > 0 ? out : null;
}

// ---------------------------------------------------------------------------
// Linux：wl-paste（Wayland）/ xclip（X11）
// ---------------------------------------------------------------------------

function isWayland(): boolean {
  return Boolean(process.env.WAYLAND_DISPLAY) || process.env.XDG_SESSION_TYPE === 'wayland';
}

function saveClipboardImageLinux(basePath: string): string | null {
  if (isWayland()) {
    const list = run('wl-paste', ['--list-types'], LIST_TIMEOUT_MS);
    if (list.ok) {
      const selected = selectImageMimeType(list.stdout.toString('utf-8').split(/\r?\n/));
      if (selected) {
        const [mime, ext] = selected;
        const data = run('wl-paste', ['--type', mime, '--no-newline'], READ_TIMEOUT_MS);
        if (data.ok && data.stdout.length > 0) {
          const filePath = `${basePath}.${ext}`;
          writeFileSync(filePath, data.stdout);
          return filePath;
        }
      }
    }
  }
  // X11 / wl-paste 失败兜底
  const targets = run('xclip', ['-selection', 'clipboard', '-t', 'TARGETS', '-o'], LIST_TIMEOUT_MS);
  if (!targets.ok) return null;
  const selected = selectImageMimeType(targets.stdout.toString('utf-8').split(/\r?\n/));
  if (!selected) return null;
  const [mime, ext] = selected;
  const data = run('xclip', ['-selection', 'clipboard', '-t', mime, '-o'], READ_TIMEOUT_MS);
  if (!data.ok || data.stdout.length === 0) return null;
  const filePath = `${basePath}.${ext}`;
  writeFileSync(filePath, data.stdout);
  return filePath;
}

// ---------------------------------------------------------------------------
// 对外接口
// ---------------------------------------------------------------------------

/**
 * 剪贴板图片写临时文件，返回文件路径；无图片或平台不支持返回 null。
 * 临时文件生命周期归系统 tmp 清理（对齐 pi：不主动删）。
 */
export async function saveClipboardImageToTemp(): Promise<string | null> {
  try {
    const basePath = join(tmpdir(), `nova-clipboard-${randomUUID()}`);
    if (process.platform === 'darwin') return saveClipboardImageMacOS(basePath);
    if (process.platform === 'linux') return saveClipboardImageLinux(basePath);
    return null; // Windows：未实现
  } catch {
    return null;
  }
}

/** 读剪贴板文本；失败或无文本返回 null。 */
export async function readClipboardText(): Promise<string | null> {
  try {
    if (process.platform === 'darwin') {
      const result = run('pbpaste', [], READ_TIMEOUT_MS);
      const text = result.stdout.toString('utf-8');
      return result.ok && text.length > 0 ? text : null;
    }
    if (process.platform === 'linux') {
      if (isWayland()) {
        const result = run('wl-paste', ['--no-newline'], READ_TIMEOUT_MS);
        const text = result.stdout.toString('utf-8');
        if (result.ok && text.length > 0) return text;
      }
      const result = run('xclip', ['-selection', 'clipboard', '-o'], READ_TIMEOUT_MS);
      const text = result.stdout.toString('utf-8');
      return result.ok && text.length > 0 ? text : null;
    }
    return null;
  } catch {
    return null;
  }
}

/** 写剪贴板文本（/copy、ctrl+x 用）；成功 true，失败/平台不支持 false。 */
export async function writeClipboardText(text: string): Promise<boolean> {
  try {
    const command =
      process.platform === 'darwin'
        ? 'pbcopy'
        : process.platform === 'linux'
          ? isWayland()
            ? 'wl-copy'
            : 'xclip'
          : null;
    if (command === null) return false;
    const args = command === 'xclip' ? ['-selection', 'clipboard'] : [];
    const result = spawnSync(command, args, {
      input: text,
      timeout: READ_TIMEOUT_MS,
    });
    return !result.error && result.status === 0;
  } catch {
    return false;
  }
}
