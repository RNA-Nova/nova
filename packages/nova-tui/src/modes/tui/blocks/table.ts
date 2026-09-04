/** table 块渲染（列对齐，表头加粗、分隔线暗色）。 */

import chalk from 'chalk';

/** table 块 → 对齐行文本。 */
export function renderTableLines(columns: string[], rows: string[][]): string {
  const widths = columns.map((col, i) =>
    Math.max(col.length, ...rows.map((row) => String(row[i] ?? '').length)),
  );
  const renderRow = (cells: string[]) =>
    cells
      .map((cell, i) => String(cell ?? '').padEnd(widths[i] ?? 0))
      .join('  ')
      .trimEnd();
  const header = chalk.bold(renderRow(columns));
  const separator = chalk.dim(widths.map((w) => '─'.repeat(w)).join('──'));
  const body = rows.map((row) => renderRow(row));
  return [header, separator, ...body].join('\n');
}
