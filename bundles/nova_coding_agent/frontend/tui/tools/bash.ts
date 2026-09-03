/**
 * bash 工具渲染器（终端风，组件形态——pi bash.ts 渲染语义对位）。
 *
 * details 契约（backend/tools/bash.py `_result_details`）：
 *   command / stdout / stderr / exit_code / duration_ms /
 *   truncated / truncated_by / full_output_path；错误时为 { error, command? }。
 *
 * 呈现语义（pi 对齐）：
 * - 折叠态：末 5 个**视觉行**（按当前宽度折行计算），上方提示隐藏行数；
 * - 底部计时行（running… / Took X.Xs）；截断警告带完整输出路径；
 * - 展示前剥掉输出末尾的 full-output footer（避免与警告行重复）。
 */
import prettyMs from 'pretty-ms';
import { Container, Text, wrapTextWithAnsi, type Component } from '@earendil-works/pi-tui';

import { detailsOf, type RendererInput } from 'nova-tui';

/** 折叠态保留的视觉行数（pi BASH_PREVIEW_LINES）。 */
const PREVIEW_VISUAL_LINES = 5;

/** 剥掉输出末尾的 full-output footer（`[... Full output: ...]` 段——pi 同款去重）。 */
function stripFullOutputFooter(text: string): string {
  return text.replace(/\n*\[[^\n]*(?:Full output|完整输出)[^\n]*\]\s*$/i, '');
}

/** 按宽度折行后的视觉行列表。 */
function toVisualLines(text: string, width: number): string[] {
  const bodyWidth = Math.max(8, width - 2);
  const out: string[] = [];
  for (const line of text.split('\n')) {
    const wrapped = wrapTextWithAnsi(line, bodyWidth);
    out.push(...(wrapped.length > 0 ? wrapped : ['']));
  }
  return out;
}

export default function renderBash(input: RendererInput): Component {
  const d = detailsOf(input);
  const colors = input.env?.colors;
  const expanded = input.env?.expanded === true;
  const dim = (s: string) => colors?.dim?.(s) ?? s;
  const warn = (s: string) => colors?.warning?.(s) ?? s;
  const err = (s: string) => colors?.error?.(s) ?? s;

  return new (class extends Container {
    override render(width: number): string[] {
      const container = new Container();

      if (typeof d.error === 'string' && d.error) {
        container.addChild(new Text(err(`执行失败：${d.error}`), 1, 0));
      }
      if (typeof d.command === 'string' && d.command) {
        container.addChild(new Text(dim(`$ ${d.command}`), 1, 0));
      }

      const stdout = typeof d.stdout === 'string' ? stripFullOutputFooter(d.stdout) : '';
      const stderr = typeof d.stderr === 'string' ? d.stderr : '';
      if (stderr) container.addChild(new Text(warn(stderr), 1, 0));

      if (stdout) {
        if (expanded) {
          container.addChild(new Text(stdout, 1, 0));
        } else {
          const visualLines = toVisualLines(stdout, width);
          if (visualLines.length > PREVIEW_VISUAL_LINES) {
            const hidden = visualLines.length - PREVIEW_VISUAL_LINES;
            container.addChild(
              new Text(dim(`... (${hidden} earlier lines, ctrl+o to expand)`), 1, 0),
            );
            container.addChild(new Text(visualLines.slice(-PREVIEW_VISUAL_LINES).join('\n'), 1, 0));
          } else {
            container.addChild(new Text(stdout, 1, 0));
          }
        }
      }

      // 底部状态行（pi 对位）：完结 "Took X.Xs"；running 态的计时行归
      // 宿主 chrome（ElapsedLine）——渲染器不读时间、不被滴答重调。
      if (typeof d.duration_ms === 'number') {
        container.addChild(new Text(dim(`Took ${prettyMs(d.duration_ms)}`), 1, 0));
      }

      // 截断警告（带完整输出路径）
      if (d.truncated === true) {
        const path = typeof d.full_output_path === 'string' ? d.full_output_path : '';
        const note = path ? `输出已截断，完整内容见 ${path}` : '输出已截断';
        container.addChild(new Text(warn(note), 1, 0));
      }
      if (typeof d.exit_code === 'number' && d.exit_code !== 0) {
        container.addChild(new Text(err(`exit code ${d.exit_code}`), 1, 0));
      }

      return container.render(width);
    }
  })();
}
