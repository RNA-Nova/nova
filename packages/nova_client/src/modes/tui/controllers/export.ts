/**
 * /export 的 HTML 导出触发（TUI 层）：拉全量条目 → 装配 → 写文件。
 * JSONL 导出不在此处（`.jsonl` 后缀走后端 /export 命令）。
 * 线上条目即 camelCase——pi 三件套（template.js）直接消费，零映射层。
 */

import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

import type { NovaUIRuntime } from 'nova-client';

import { assembleHtml } from '../../../export/index.js';
import { getExportThemeData } from '../themes/index.js';
import type { TranscriptController } from './transcript.js';

/** HTML 导出（无参数或 .html 路径走这里；.jsonl 走后端）。 */
export async function exportSessionHtml(
  runtime: NovaUIRuntime,
  transcript: TranscriptController,
  cwd: string,
  rawPath: string | undefined,
): Promise<void> {
  const outputPath = await writeSessionHtml(runtime, cwd, rawPath);
  transcript.addInfo(`已导出 HTML: ${outputPath}`);
}

/** 装配并写出会话 HTML，返回输出路径（不产出用户消息——/share 等调用方自管反馈）。 */
export async function writeSessionHtml(
  runtime: NovaUIRuntime,
  cwd: string,
  rawPath: string | undefined,
): Promise<string> {
  const [entriesResult, snapshot] = await Promise.all([
    runtime.invoke('getSessionEntries', {}),
    runtime.invoke('getSessionState', {}),
  ]);
  const entries = (entriesResult as { entries?: unknown[] }).entries ?? [];
  const snap = snapshot as unknown as Record<string, unknown>;

  const outputPath = resolve(
    cwd,
    rawPath ?? `nova-session-${String(snap.sessionId ?? 'export').slice(0, 8)}.html`,
  );
  const sessionData = {
    header: {
      id: snap.sessionId ?? '',
      cwd: snap.cwd ?? cwd,
      timestamp: Date.now(),
    },
    entries,
    leafId: snap.leafId ?? null,
    renderedTools: {},
  };
  const html = assembleHtml({ sessionData, theme: getExportThemeData() });
  writeFileSync(outputPath, html, 'utf-8');
  return outputPath;
}
