/**
 * diff 块渲染（行内词级高亮，适配块结构）。
 *
 * - 上下文行：灰；
 * - 删除行：红；新增行：绿；
 * - 恰好 1 删 + 1 增相邻（单行修改）→ 词级 diff，变动 token 反色高亮
 *   （intra-line 规则；del/add 数量不等则整行平铺）。
 */

import * as Diff from 'diff';
import chalk from 'chalk';
import type { NovaBlock } from 'nova-tui';

import { colors } from '../themes/index.js';

type DiffBlock = Extract<NovaBlock, { kind: 'diff' }>;
type Line = DiffBlock['hunks'][number]['lines'][number];

/** tab 替换为空格（渲染一致性）。 */
function replaceTabs(text: string): string {
  return text.replace(/\t/g, '   ');
}

/** 词级 diff：变动 token 反色（theme.inverse → chalk）。 */
function renderIntraLineDiff(
  oldContent: string,
  newContent: string,
): { removedLine: string; addedLine: string } {
  const wordDiff = Diff.diffWords(oldContent, newContent);

  let removedLine = '';
  let addedLine = '';
  let isFirstRemoved = true;
  let isFirstAdded = true;

  for (const part of wordDiff) {
    if (part.removed) {
      let value = part.value;
      if (isFirstRemoved) {
        const leadingWs = value.match(/^(\s*)/)?.[1] || '';
        value = value.slice(leadingWs.length);
        removedLine += leadingWs;
        isFirstRemoved = false;
      }
      if (value) removedLine += chalk.inverse(value);
    } else if (part.added) {
      let value = part.value;
      if (isFirstAdded) {
        const leadingWs = value.match(/^(\s*)/)?.[1] || '';
        value = value.slice(leadingWs.length);
        addedLine += leadingWs;
        isFirstAdded = false;
      }
      if (value) addedLine += chalk.inverse(value);
    } else {
      removedLine += part.value;
      addedLine += part.value;
    }
  }

  return { removedLine, addedLine };
}

/** diff 块 → 染色行文本。 */
export function renderDiffLines(block: DiffBlock): string {
  const result: string[] = [];

  for (const hunk of block.hunks) {
    if (hunk.header) result.push(colors.dim(hunk.header));

    const lines = hunk.lines;
    let i = 0;
    while (i < lines.length) {
      const line = lines[i]!;

      if (line.type === 'del') {
        // 收集连续删除行
        const removed: Line[] = [];
        while (i < lines.length && lines[i]!.type === 'del') {
          removed.push(lines[i]!);
          i++;
        }
        // 收集连续新增行
        const added: Line[] = [];
        while (i < lines.length && lines[i]!.type === 'add') {
          added.push(lines[i]!);
          i++;
        }

        if (removed.length === 1 && added.length === 1) {
          const { removedLine, addedLine } = renderIntraLineDiff(
            replaceTabs(removed[0]!.text),
            replaceTabs(added[0]!.text),
          );
          result.push(colors.toolDiffRemoved(`- ${removedLine}`));
          result.push(colors.toolDiffAdded(`+ ${addedLine}`));
        } else {
          for (const r of removed) {
            result.push(colors.toolDiffRemoved(`- ${replaceTabs(r.text)}`));
          }
          for (const a of added) {
            result.push(colors.toolDiffAdded(`+ ${replaceTabs(a.text)}`));
          }
        }
        continue;
      }

      if (line.type === 'add') {
        result.push(colors.toolDiffAdded(`+ ${replaceTabs(line.text)}`));
        i++;
        continue;
      }

      result.push(colors.toolDiffContext(`  ${replaceTabs(line.text)}`));
      i++;
    }
  }

  return result.join('\n');
}
