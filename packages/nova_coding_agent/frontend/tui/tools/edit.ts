/**
 * edit 工具渲染器（diff 风，组件形态）。
 *
 * details 契约（backend/tools/edit.py）：
 *   path / patch（标准 unified patch，引擎执行时已生成）
 *   old / new（实际基础内容与新内容）/ first_changed_line；
 *   错误时为 { error, path? }。
 * 优先消费执行期生成的 patch；patch 缺失时降级为 old/new 整体替换
 * （不重算模糊匹配）。词级高亮经宿主 diff 行渲染器（renderDiffLines）。
 *
 * 执行前预览（pi edit.ts renderCall 的 argsComplete 分支对位）：
 * 命名导出 preview 钩子——参数完整、执行未开始时由组件层调用，
 * 只读匹配并生成 patch；renderEdit 经 input.preview 消费
 * （执行后 details.patch 到达即取代预览，二者同形状无缝衔接）。
 */
import { Container, Text, type Component } from '@earendil-works/pi-tui';

import {
  detailsOf,
  type DiffHunk,
  type DiffLine,
  type RendererInput,
} from 'nova-client';
import { renderDiffLines } from 'nova-client/modes/tui/blocks/diff';

import { computeEditPreview, type PreviewEdit } from '../lib/edit-preview.js';

/** preview 钩子（框架契约：PreviewComputer）。只读——不写盘、无副作用。 */
export async function preview(
  args: Record<string, unknown>,
  cwd: string,
): Promise<unknown> {
  const path = typeof args.path === 'string' ? args.path : undefined;
  const edits = Array.isArray(args.edits) ? (args.edits as PreviewEdit[]) : undefined;
  if (!path || !edits || edits.length === 0) return undefined;
  return computeEditPreview(path, edits, cwd);
}

/** 把标准 unified patch 文本解析为 diff hunk 列表。 */
function parseUnifiedPatch(patch: string): DiffHunk[] {
  const hunks: DiffHunk[] = [];
  let current: DiffHunk | undefined;

  for (const raw of patch.split('\n')) {
    if (raw.startsWith('@@')) {
      current = { header: raw, lines: [] };
      hunks.push(current);
      continue;
    }
    if (raw.startsWith('---') || raw.startsWith('+++') || raw.startsWith('\\')) {
      continue; // 文件头与 "\ No newline" 标记不进入行列表
    }
    if (!current) continue;
    const type: DiffLine['type'] = raw.startsWith('+')
      ? 'add'
      : raw.startsWith('-')
        ? 'del'
        : 'context';
    current.lines.push({ type, text: raw.slice(1) });
  }
  return hunks;
}

export default function renderEdit(input: RendererInput): Component {
  const d = detailsOf(input);
  const colors = input.env?.colors;
  const dim = (s: string) => colors?.dim?.(s) ?? s;
  const err = (s: string) => colors?.error?.(s) ?? s;

  const container = new Container();
  const addDiff = (hunks: DiffHunk[], path?: string) => {
    container.addChild(
      new Text(renderDiffLines({ kind: 'diff', hunks, oldPath: path, newPath: path }), 1, 0),
    );
  };

  if (typeof d.error === 'string' && d.error) {
    container.addChild(new Text(err(`编辑失败：${d.error}`), 1, 0));
    return container;
  }

  const path = typeof d.path === 'string' ? d.path : undefined;

  if (typeof d.patch === 'string' && d.patch) {
    const hunks = parseUnifiedPatch(d.patch);
    if (hunks.length > 0) {
      addDiff(hunks, path);
      return container;
    }
  }

  // 执行前预览（argsComplete 时点由 preview 钩子算好注入）：同走 patch 通道
  const previewData =
    typeof input.preview === 'object' && input.preview !== null
      ? (input.preview as { patch?: unknown; path?: unknown; error?: unknown })
      : undefined;
  if (previewData && typeof previewData.error === 'string' && previewData.error) {
    container.addChild(new Text(err(`预览匹配失败：${previewData.error}`), 1, 0));
    return container;
  }
  if (previewData && typeof previewData.patch === 'string' && previewData.patch) {
    const hunks = parseUnifiedPatch(previewData.patch);
    if (hunks.length > 0) {
      const previewPath = typeof previewData.path === 'string' ? previewData.path : path;
      container.addChild(new Text(dim('预览（尚未执行）'), 1, 0));
      addDiff(hunks, previewPath);
      return container;
    }
  }

  // patch 缺失（自定义/历史 edit 结果）：old/new 整体替换
  if (typeof d.old === 'string' && typeof d.new === 'string') {
    const lines: DiffLine[] = [
      ...d.old.split('\n').map((text) => ({ type: 'del' as const, text })),
      ...d.new.split('\n').map((text) => ({ type: 'add' as const, text })),
    ];
    addDiff([{ lines }], path);
  }

  return container;
}
