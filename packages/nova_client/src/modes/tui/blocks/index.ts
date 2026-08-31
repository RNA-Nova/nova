/**
 * 块适配层：声明式块（NovaBlock）→ pi-tui 组件，注册制形态。
 *
 * 纪律：
 * - 官方五种块（markdown/code/diff/table/json）经 ``registerBuiltinBlocks``
 *   注册为 ``block:<kind>`` slot（source=``builtin``）——与包作者将来的
 *   自定义块走同一 register（dogfood，无内建特权通道）；
 * - 块数据先过 ``validateBlock``（渲染器是第三方纯函数，形状不设防）——
 *   非法块渲染为错误块（红字列问题），不炸适配器；
 * - 未注册 kind 降级为 json 展示（开放集兜底）+ dim 提示行（可发现性）。
 */

import chalk from 'chalk';
import {
  blockSlot,
  validateBlock,
  type ExtensionUIAPI,
  type NovaBlock,
  type SlotRegistry,
} from 'nova-client';
import { Markdown, Text, type Component } from '@earendil-works/pi-tui';

import { markdownTheme } from '../themes/index.js';
import { renderDiffLines } from './diff.js';
import { renderTableLines } from './table.js';

/** 块适配器签名（slot producer：块 → pi-tui 组件）。 */
export type BlockAdapter = (block: NovaBlock) => Component;

// ---------------------------------------------------------------------------
// 官方块适配器（pi-tui 现成组件优先；diff/table 无对应物自绘行）
// ---------------------------------------------------------------------------

const markdownAdapter: BlockAdapter = (block) => {
  const { text } = block as Extract<NovaBlock, { kind: 'markdown' }>;
  return new Markdown(text, 1, 0, markdownTheme);
};

const codeAdapter: BlockAdapter = (block) => {
  const { text, language } = block as Extract<NovaBlock, { kind: 'code' }>;
  const fenced = `\`\`\`${language ?? ''}\n${text}\n\`\`\``;
  return new Markdown(fenced, 1, 0, markdownTheme);
};

const diffAdapter: BlockAdapter = (block) => {
  const diff = block as Extract<NovaBlock, { kind: 'diff' }>;
  return new Text(renderDiffLines(diff), 1, 0);
};

const tableAdapter: BlockAdapter = (block) => {
  const { columns, rows } = block as Extract<NovaBlock, { kind: 'table' }>;
  return new Text(renderTableLines(columns, rows), 1, 0);
};

const jsonAdapter: BlockAdapter = (block) => {
  const { data } = block as Extract<NovaBlock, { kind: 'json' }>;
  return new Text(chalk.dim(JSON.stringify(data, null, 2)), 1, 0);
};

/** 官方块适配器表（kind → adapter）。 */
export const BUILTIN_BLOCK_ADAPTERS: ReadonlyArray<readonly [string, BlockAdapter]> = [
  ['markdown', markdownAdapter],
  ['code', codeAdapter],
  ['diff', diffAdapter],
  ['table', tableAdapter],
  ['json', jsonAdapter],
];

/** 注册官方块适配器（宿主经 runtime slotsBootstrap 注入——走同一 ExtensionUIAPI）。 */
export function registerBuiltinBlocks(api: ExtensionUIAPI): void {
  for (const [kind, adapter] of BUILTIN_BLOCK_ADAPTERS) {
    api.registerBlock(kind, { adapter });
  }
}

// ---------------------------------------------------------------------------
// 消费点：校验 → 查表 → 降级
// ---------------------------------------------------------------------------

/** 校验失败块（红字列问题——渲染器 bug 可见，不静默炸）。 */
function invalidBlockComponent(kind: string, issues: string[]): Component {
  return new Text(
    chalk.red(`✗ 非法块（kind: ${kind}）`) + chalk.dim(`\n  ${issues.join('\n  ')}`),
    1,
    0,
  );
}

/** 未注册 kind 降级（json 展示原块 + dim 提示——开放集兜底）。 */
function unknownKindComponent(block: NovaBlock): Component {
  const kind = (block as { kind?: unknown }).kind;
  return new Text(
    chalk.dim(`[未注册块类型: ${String(kind)}]`) +
      '\n' +
      chalk.dim(JSON.stringify(block, null, 2)),
    1,
    0,
  );
}

/** 单个块 → 组件（校验 → 注册表解析 → 自定义 validator → 未注册降级）。 */
export function blockToComponent(block: NovaBlock, slots: SlotRegistry): Component {
  const validation = validateBlock(block);
  if (!validation.ok) return invalidBlockComponent(validation.kind, validation.issues);

  const adapter = slots.resolve<NovaBlock, Component>(blockSlot(block.kind));
  if (!adapter) return unknownKindComponent(block);

  // 自定义块的可选 schema 钩子（registerBlock 的 validate——内建五种已过 validateBlock）
  const validator = slots.resolveBlockValidator(block.kind);
  if (validator !== undefined) {
    const issues = validator(block as Record<string, unknown>);
    if (issues.length > 0) return invalidBlockComponent(block.kind, issues);
  }
  return adapter(block);
}

/** 块列表 → 组件列表。 */
export function blocksToComponents(blocks: NovaBlock[], slots: SlotRegistry): Component[] {
  return blocks.map((block) => blockToComponent(block, slots));
}
