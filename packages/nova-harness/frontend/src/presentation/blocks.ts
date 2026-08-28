/**
 * 声明式块词汇 v1（框架无关）。
 *
 * 块是渲染器与前端之间的唯一契约：渲染器是纯函数（数据 → 块），
 * 前端把块适配为框架组件（pi-tui / DOM）。本文件**不进 Python**——
 * Python 的哑管道只发 details 平铺数据，块是 Node 层的呈现词汇。
 *
 * 开放集：扩展可经 slot 注册新块类型（``block:<kind>``）；
 * 前端遇到未注册块类型时降级为 ``json`` 块展示。
 */

// ---------------------------------------------------------------------------
// 块类型
// ---------------------------------------------------------------------------

import type { ToolCallItem } from '../protocol/nova-wire.gen.js';

/** diff 块的一行。 */
export interface DiffLine {
  type: 'add' | 'del' | 'context';
  text: string;
}

/** diff 块的一个 hunk。 */
export interface DiffHunk {
  /** 可选头（如 ``@@ -1,4 +1,5 @@``）。 */
  header?: string;
  lines: DiffLine[];
}

/** 声明式块（v1 五种内建 + 开放集兜底）。 */
export type NovaBlock =
  | { kind: 'diff'; hunks: DiffHunk[]; oldPath?: string; newPath?: string }
  | { kind: 'markdown'; text: string }
  | { kind: 'code'; text: string; language?: string; title?: string }
  | { kind: 'json'; data: unknown }
  | { kind: 'table'; columns: string[]; rows: string[][] }
  // 开放集：自定义块（registerBlock 注册的 kind）。内建五种的字段错误在
  // 类型层会被兜底成员吞（TS 无 negated types）——运行时 validateBlock
  // 是真正的守门人（类型层窄化照常工作：Extract/判别不受影响）。
  | ({ kind: string } & Record<string, unknown>);

// ---------------------------------------------------------------------------
// 渲染器契约
// ---------------------------------------------------------------------------

/** 工具结果的一个片段（content/details 均为工具作者的自由负载）。 */
export interface RendererResultPart {
  content?: unknown;
  details?: unknown;
}

/**
 * 渲染器输入：工具调用 item（服务器归约成品）+ 预览 + 宿主环境。
 *
 * 终态（server-item-layer 设计）：渲染器直接消费线上 ``ToolCallItem``——
 * 不再有前端侧的中间卡片模型（旧 ToolCallCard 已随 mirror 归约消亡）。
 * item 字段即数据源：
 * - ``status``：pending = 参数流式累积中（执行未开始）；running = 执行中；
 *   done/failed/cancelled = 终态（declined 归审批扩展产出）；
 * - ``args``：参数（pending 期随流式增量替换）；
 * - ``partialResult``/``result``：{ content, details } 自由负载；
 * - ``durationMs``/``error``：终态元数据/错误文本。
 */
export interface RendererInput {
  item: ToolCallItem;
  /**
   * 执行前预览数据（渲染器模块 preview 钩子的产出，由组件层异步算好注入；
   * 形状由工具自己定义——edit 为 { patch, path } 或 { error }）。
   */
  preview?: unknown;
  /** 宿主环境（组件形态渲染器的主题/上下文通道——见 RendererEnv）。 */
  env?: RendererEnv;
}

/**
 * 渲染器宿主环境（组件形态渲染器的主题/上下文通道）。
 * 框架无关层结构化承载——TUI 宿主注入语义色表与 Markdown 主题；
 * 组件形态渲染器经它取色，不 import 宿主内部模块（第三方纪律）。
 */
export interface RendererEnv {
  cwd: string;
  /** 语义色表（token → 色函数；TUI 为 46 token ThemeColors）。 */
  colors: Record<string, (s: string) => string>;
  /** Markdown 主题（内嵌 Markdown 组件时使用；宿主特定类型以 unknown 承载）。 */
  markdownTheme?: unknown;
  /** 全局展开态（ctrl+o）——折叠语义的渲染器据此决定预览/全量。 */
  expanded?: boolean;
  // 计时显示归宿主 chrome（ElapsedLine——pi Loader 自转对位）：渲染器
  // 不读时间、不被滴答重调——渲染器是纯函数，调用只随真实输入变更发生。
}

/**
 * 执行前预览计算器（渲染器模块的可选命名导出）。
 *
 * 纪律：渲染器（默认导出）是纯函数，不能碰 IO；preview 钩子是包作者提供的
 * 唯一副作用通道——只读计算（edit：读文件 + 匹配 + 生成 patch），
 * 由组件层在 argsComplete && !执行开始 时调用，产出经 RendererInput.preview
 * 回注渲染器。返回 undefined 表示无预览（走通用呈现）。
 */
export type PreviewComputer = (
  args: Record<string, unknown>,
  cwd: string,
) => Promise<unknown>;

/**
 * 渲染器产出（双形态，判别在消费点）：
 * - ``NovaBlock[]``：声明式块（数据——可过网、schema 校验、宿主共享适配器）；
 * - 宿主组件（带 render 方法的对象——TUI 为 pi-tui Component）：全能力形态，
 *   状态/交互/自绘随意；**仅同进程宿主可用**（Web 宿主降级为通用卡片）。
 */
export type RendererOutput = NovaBlock[] | { render: (width: number) => string[] };

/** 渲染器即纯函数：输入卡片，产出块列表或宿主组件（空列表 = 用通用回退）。 */
export type NovaRenderer = (input: RendererInput) => RendererOutput;

/** 产出形态判别（消费点用）：数组 → 块列表；带 render 方法的对象 → 组件。 */
export function isComponentOutput(
  output: RendererOutput | undefined,
): output is { render: (width: number) => string[] } {
  return (
    typeof output === 'object' &&
    output !== null &&
    !Array.isArray(output) &&
    typeof (output as { render?: unknown }).render === 'function'
  );
}

/**
 * 自定义块校验器（registerBlock 的可选 schema 钩子）：
 * 输入未知形状的块数据，返回问题清单（空数组 = 合法）。
 * 消费层在适配前调用——非空 issues 渲染为错误块（不炸适配器）。
 */
export type BlockValidator = (block: Record<string, unknown>) => string[];

// ---------------------------------------------------------------------------
// 块 schema 校验（外部数据守护：渲染器是第三方纯函数，产出形状不设防）
// ---------------------------------------------------------------------------

/** 块校验结果：ok 原样通过；issues 列出全部问题（消费层渲染错误块）。 */
export type BlockValidation =
  | { ok: true; block: NovaBlock }
  | { ok: false; kind: string; issues: string[] };

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

/**
 * 校验声明式块（内建五种 kind 查必需字段；**未知 kind 放行**——开放集，
 * 由注册的自定义块适配器接管，消费层无注册时降级 json 展示）。
 */
export function validateBlock(value: unknown): BlockValidation {
  const fail = (kind: string, issues: string[]): BlockValidation => ({ ok: false, kind, issues });
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return fail('<非对象>', ['块必须是对象']);
  }
  const kind = (value as { kind?: unknown }).kind;
  if (typeof kind !== 'string') return fail('<缺 kind>', ['块缺 kind 字段']);

  const record = value as Record<string, unknown>;
  switch (kind) {
    case 'markdown':
      return typeof record.text === 'string'
        ? { ok: true, block: value as NovaBlock }
        : fail(kind, ['text 必须是字符串']);
    case 'code': {
      const issues: string[] = [];
      if (typeof record.text !== 'string') issues.push('text 必须是字符串');
      if (record.language !== undefined && typeof record.language !== 'string') {
        issues.push('language 必须是字符串');
      }
      if (record.title !== undefined && typeof record.title !== 'string') {
        issues.push('title 必须是字符串');
      }
      return issues.length === 0 ? { ok: true, block: value as NovaBlock } : fail(kind, issues);
    }
    case 'json':
      return { ok: true, block: value as NovaBlock }; // data 任意（含缺失）
    case 'table': {
      const issues: string[] = [];
      if (!isStringArray(record.columns)) issues.push('columns 必须是字符串数组');
      if (
        !Array.isArray(record.rows) ||
        !record.rows.every((row) => Array.isArray(row))
      ) {
        issues.push('rows 必须是数组的数组');
      }
      return issues.length === 0 ? { ok: true, block: value as NovaBlock } : fail(kind, issues);
    }
    case 'diff': {
      const issues: string[] = [];
      const hunks = record.hunks;
      if (!Array.isArray(hunks)) {
        issues.push('hunks 必须是数组');
      } else {
        hunks.forEach((hunk, i) => {
          if (typeof hunk !== 'object' || hunk === null || !Array.isArray(hunk.lines)) {
            issues.push(`hunks[${i}] 缺 lines 数组`);
            return;
          }
          hunk.lines.forEach((line: unknown, j: number) => {
            const bad =
              typeof line !== 'object' ||
              line === null ||
              !['add', 'del', 'context'].includes((line as { type?: unknown }).type as string) ||
              typeof (line as { text?: unknown }).text !== 'string';
            if (bad) issues.push(`hunks[${i}].lines[${j}] 形状非法（type∈add/del/context + text 字符串）`);
          });
        });
      }
      if (record.oldPath !== undefined && typeof record.oldPath !== 'string') {
        issues.push('oldPath 必须是字符串');
      }
      if (record.newPath !== undefined && typeof record.newPath !== 'string') {
        issues.push('newPath 必须是字符串');
      }
      return issues.length === 0 ? { ok: true, block: value as NovaBlock } : fail(kind, issues);
    }
    default:
      return { ok: true, block: value as NovaBlock }; // 未知 kind：开放集放行
  }
}

// ---------------------------------------------------------------------------
// 渲染器共用的小工具
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

/** 读取 details（result 优先，partialResult 兜底）。 */
export function detailsOf(input: RendererInput): Record<string, unknown> {
  const partial = input.item.partialResult as RendererResultPart | undefined;
  const result = input.item.result as RendererResultPart | undefined;
  return {
    ...asRecord(partial?.details),
    ...asRecord(result?.details),
  };
}

/** 从 content（文本块数组或字符串）提取纯文本。 */
export function extractText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map((block) => {
      if (typeof block === 'object' && block !== null && 'text' in block) {
        const text = (block as { text?: unknown }).text;
        if (typeof text === 'string') return text;
      }
      return '';
    })
    .join('');
}
