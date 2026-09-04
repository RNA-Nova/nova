/**
 * 启动流程编排。
 *
 * 三块能力：
 * - **CLI 解析纯函数**：@file 展开、初始消息拼接、thinking 校验、
 *   compaction 计数——全部可单测；
 * - **StartupFlags**：main.ts 的解析产物（经 app 装配透传进
 *   runtime options 与本控制器）；
 * - **StartupController**：app 启动后（runtime.start 完成）的一次性编排——
 *   未信任项目横幅、compaction 提示、--name 命名、--resume 打开会话选择器。
 *
 * 装配说明：本控制器由 app.ts 在 `runtime.start()` 之后实例化并调用
 * `runPostStart()`（装配点见交付报告）；main.ts 只负责解析与透传，
 * 不直接消费本控制器（sessions/transcript 句柄归 app 装配根）。
 */

import { readFile, stat } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';

import type { RuntimeHost } from '../../../runtime.js';
import type { SessionSnapshot } from '../../../mirror/types.js';

/** main.ts 解析出的启动 flags（app 装配透传进 runtime options / StartupController）。 */
export interface StartupFlags {
  /** --session <file|id>：createSession 的 sessionFile（绝对路径直接用；裸 id 由后端在 cwd 会话目录解析）。 */
  sessionFile?: string;
  /** --thinking <level>：createSession 的 thinkingLevel。 */
  thinking?: string;
  /** -r/--resume：启动后打开会话选择器（推 '/resume' 命令——bundle 包自持 UI）。 */
  resume?: boolean;
  /** -n/--name <name>：启动后 setSessionName。 */
  sessionName?: string;
  /** --no-session：不持久化（内存态运行——createSession.noSession 契约直通）。 */
  noSession?: boolean;
}

/** 合法思考级别（与契约 SetThinkingLevelParams.level 枚举一致）。 */
export const VALID_THINKING_LEVELS = [
  'off',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
] as const;

export function isValidThinkingLevel(level: string): boolean {
  return (VALID_THINKING_LEVELS as readonly string[]).includes(level);
}

/** 启动期可预期错误（@file 缺失/读取失败等）——main.ts 捕获后报错退出。 */
export class StartupError extends Error {}

/** [message...] 词元分组：@path 前缀为文件参数；孤立 "@" 按字面文本。 */
export function splitMessageTokens(parts: readonly string[]): {
  messageTokens: string[];
  fileArgs: string[];
} {
  const messageTokens: string[] = [];
  const fileArgs: string[] = [];
  for (const part of parts) {
    if (part.startsWith('@') && part.length > 1) fileArgs.push(part.slice(1));
    else messageTokens.push(part);
  }
  return { messageTokens, fileArgs };
}

/** 展开 ~/ 前缀（`~other` 不展开）。 */
export function expandTildePath(path: string): string {
  if (path === '~') return homedir();
  if (path.startsWith('~/')) return join(homedir(), path.slice(2));
  return path;
}

/**
 * --session 参数归一：路径形态（含 `/`、`\`
 * 或以 .jsonl 结尾）解析为绝对路径；裸 id 原样透传（后端在 cwd 会话目录解析）。
 */
export function resolveSessionArg(arg: string, cwd: string): string {
  if (arg.includes('/') || arg.includes('\\') || arg.endsWith('.jsonl')) {
    return resolve(cwd, expandTildePath(arg));
  }
  return arg;
}

/** 图片魔数嗅探（png/jpeg/gif/webp——与后端 read 工具的支持集一致）。 */
export function detectImageMimeType(bytes: Uint8Array): string | null {
  if (
    bytes.length >= 8 &&
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47
  ) {
    return 'image/png';
  }
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) {
    return 'image/jpeg';
  }
  if (
    bytes.length >= 6 &&
    bytes[0] === 0x47 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x38
  ) {
    return 'image/gif';
  }
  if (
    bytes.length >= 12 &&
    bytes[0] === 0x52 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x46 &&
    bytes[8] === 0x57 &&
    bytes[9] === 0x45 &&
    bytes[10] === 0x42 &&
    bytes[11] === 0x50
  ) {
    return 'image/webp';
  }
  return null;
}

/**
 * @file 参数展开：
 * - 文本文件 → ``<file name="...">内容</file>`` 内联；
 * - 图片（魔数嗅探命中）→ ImageContent 附件 + 空体 ``<file>`` 引用（模型经附件看图，引用占位标记来源）；
 * 空文件跳过；缺失/目录/不可读抛 StartupError。
 * 无 auto-resize（无 TS 图像库——原图直传；压缩归后端 read 路径）。
 */
export async function expandFileArguments(
  fileArgs: readonly string[],
  cwd: string,
): Promise<{ text: string; images: Array<{ type: 'image'; data: string; mimeType: string }> }> {
  let text = '';
  const images: Array<{ type: 'image'; data: string; mimeType: string }> = [];
  for (const fileArg of fileArgs) {
    const absolutePath = resolve(cwd, expandTildePath(fileArg));
    let stats;
    try {
      stats = await stat(absolutePath);
    } catch {
      throw new StartupError(`文件不存在：${absolutePath}（@${fileArg}）`);
    }
    if (stats.isDirectory()) {
      throw new StartupError(`@参数指向目录而非文件：${absolutePath}（@${fileArg}）`);
    }
    if (stats.size === 0) continue; // 空文件跳过

    const bytes = await readFile(absolutePath);
    const mimeType = detectImageMimeType(bytes);
    if (mimeType) {
      images.push({
        type: 'image',
        data: Buffer.from(bytes).toString('base64'),
        mimeType,
      });
      text += `<file name="${absolutePath}"></file>\n`;
      continue;
    }
    try {
      const content = bytes.toString('utf-8');
      text += `<file name="${absolutePath}">\n${content}\n</file>\n`;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new StartupError(`无法读取文件 ${absolutePath}：${message}`);
    }
  }
  return { text, images };
}

/**
 * 拼接首条消息：文件块在前、消息文本在后（—
 * nova 无 stdin/图片通道）。全空返回 undefined（不触发首条提交）。
 */
export function buildInitialMessage(
  fileText: string,
  messageTokens: readonly string[],
): string | undefined {
  const parts: string[] = [];
  if (fileText) parts.push(fileText.trimEnd());
  const message = messageTokens.join(' ');
  if (message) parts.push(message);
  return parts.length > 0 ? parts.join('\n') : undefined;
}

/** 会话历史中的 compaction 条目计数（—防御式解析未知条目）。 */
export function countCompactionEntries(entries: readonly unknown[]): number {
  let count = 0;
  for (const entry of entries) {
    if (
      typeof entry === 'object' &&
      entry !== null &&
      (entry as { type?: unknown }).type === 'compaction'
    ) {
      count += 1;
    }
  }
  return count;
}

/** compaction 提示文案。 */
export function formatCompactionHint(count: number): string {
  return `会话已压缩 ${count} 次`;
}

/** 未信任项目横幅文案（—含 /trust 提示）。 */
export const TRUST_BANNER_TEXT =
  '警告：本项目未被信任——项目级 .nova 资源与包已忽略。使用 /trust 保存信任决策后重启 nova。';

export interface StartupControllerDeps {
  runtime: Pick<RuntimeHost, 'invoke'>;
  /** 提示出口（TranscriptController.addInfo——warning 级样式条目挂账，先走 info）。 */
  transcript: { addInfo(message: string): void };
  /** 会话选择器（--resume 触发点；装配注入——未注入时 --resume 静默跳过）。 */
  sessions?: { open(): Promise<void> };}

/**
 * 启动后一次性编排（app 在 runtime.start() 完成后调用 runPostStart）。
 * 全部为提示性能力：单项失败不阻断后续与启动。
 */
export class StartupController {
  constructor(
    private readonly deps: StartupControllerDeps,
    private readonly flags: StartupFlags = {},
  ) {}

  /** 总入口（横幅 → 压缩提示 → 命名 → resume 选择器）。 */
  async runPostStart(snapshot: SessionSnapshot | null): Promise<void> {
    this.showTrustBannerIfNeeded(snapshot);
    this.showCapabilityReportIfNeeded(snapshot);
    await this.showCompactionHintIfNeeded();
    await this.applySessionNameIfNeeded();
    await this.openResumeSelectorIfRequested();
  }

  /** 未信任项目横幅（快照 projectTrusted === false 时）。 */
  showTrustBannerIfNeeded(snapshot: Pick<SessionSnapshot, 'projectTrusted'> | null): void {
    if (snapshot?.projectTrusted !== false) return;
    this.deps.transcript.addInfo(TRUST_BANNER_TEXT);
  }

  /** 角色能力选配问题（快照 capabilityReport 非空项——"角色少工具"的确定性答案）。 */
  showCapabilityReportIfNeeded(
    snapshot: Pick<SessionSnapshot, 'capabilityReport'> | null,
  ): void {
    const report = snapshot?.capabilityReport;
    if (!report || report.length === 0) return;
    const REASONS: Record<string, string> = {
      missing: '未安装（任何源都没有这个名字）',
      disabledBySettings: '已被 settings 禁用',
      disabledBySdk: '已被宿主环境禁用',
    };
    for (const item of report) {
      const reason = REASONS[item.status] ?? item.status;
      this.deps.transcript.addInfo(
        `角色选配未生效：${item.resourceType}/${item.name} —— ${reason}`,
      );
    }
  }

  /** 恢复/continue 会话的压缩历史提示（N>0 时 transcript 提示一行）。 */
  async showCompactionHintIfNeeded(): Promise<void> {
    let entries: unknown[];
    try {
      const result = await this.deps.runtime.invoke('getSessionEntries', {});
      entries = result.entries;
    } catch {
      return; // 提示性能力——拉取失败不阻断启动
    }
    const count = countCompactionEntries(entries);
    if (count > 0) this.deps.transcript.addInfo(formatCompactionHint(count));
  }

  /** --name：启动后命名会话（失败仅提示，不阻断）。 */
  async applySessionNameIfNeeded(): Promise<void> {
    const name = this.flags.sessionName;
    if (!name) return;
    try {
      await this.deps.runtime.invoke('setSessionName', { name });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.deps.transcript.addInfo(`设置会话名失败：${message}`);
    }
  }

  /** --resume：启动后打开会话选择器（选择器自身对"已有对话框"做叠加防护）。 */
  async openResumeSelectorIfRequested(): Promise<void> {
    if (!this.flags.resume || !this.deps.sessions) return;
    await this.deps.sessions.open();
  }
}
