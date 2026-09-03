/**
 * subagent 工具渲染器（组件形态——pi examples/extensions/subagent 渲染语义对位）。
 *
 * details 契约（backend/tools/subagent.py，键名 snake 随 wire 原样透传）：
 *   { mode: 'single'|'parallel'|'chain',
 *     results: [{ agent, agent_source, task, output, error, error_message,
 *                 exit_code,      // -1 = 运行中占位（parallel 流式）
 *                 usage: { input_tokens, output_tokens, cache_read, cache_write,
 *                          cost, context_tokens, turns },   // 工具自产 snake
 *                 model, stop_reason, stderr,
 *                 messages: [...] } ] }  // 子会话 wire 消息（camelCase 原样负载）
 *
 * 注意两个键名域：工具 details 层是 snake（自由负载不转换），内嵌 messages
 * 是子进程 JSONL 事件里的 wire 消息（camelCase）——本文件两个读取面分开处理。
 *
 * 呈现语义（pi 对齐）：
 * - streaming/running：头部（模式 + 规模）+ 逐任务实时状态
 *   （⏳ 运行中 / ✓ / ✗，并行含 "n/m done, k running"）；
 * - 折叠态：单/链/并行均显示末 N 个展示项（工具调用格式化行 + 文本预览）；
 * - 展开态（ctrl+o）：完整 task、全部工具调用、终输出 Markdown 渲染、
 *   每步/每任务 usage 行与总计行。
 */
import { Container, Markdown, Spacer, Text, type Component } from '@earendil-works/pi-tui';

import { detailsOf, type RendererInput } from 'nova-tui';

import type { MarkdownTheme } from '@earendil-works/pi-tui';

/** 折叠态展示的末尾条目数（pi COLLAPSED_ITEM_COUNT）。 */
const COLLAPSED_ITEM_COUNT = 10;
/** 链/并行折叠态每步（任务）展示的条目数。 */
const PER_STEP_COLLAPSED = 5;

// ---------------------------------------------------------------------------
// details 数据形态（snake 域）
// ---------------------------------------------------------------------------

interface SubagentUsage {
  input_tokens?: number;
  output_tokens?: number;
  cache_read?: number;
  cache_write?: number;
  cost?: number;
  context_tokens?: number;
  turns?: number;
}

interface SubagentResultItem {
  agent?: string;
  agent_source?: string | null;
  task?: string;
  output?: string;
  error?: string | null;
  error_message?: string | null;
  exit_code?: number;
  usage?: SubagentUsage;
  model?: string | null;
  stop_reason?: string | null;
  stderr?: string;
  messages?: unknown[];
}

interface SubagentDetails {
  mode?: 'single' | 'parallel' | 'chain';
  results?: SubagentResultItem[];
}

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

function formatTokens(count: number): string {
  if (count < 1000) return count.toString();
  if (count < 10000) return `${(count / 1000).toFixed(1)}k`;
  if (count < 1000000) return `${Math.round(count / 1000)}k`;
  return `${(count / 1000000).toFixed(1)}M`;
}

function formatUsageStats(usage: SubagentUsage, model?: string | null): string {
  const parts: string[] = [];
  if (usage.turns) parts.push(`${usage.turns} turn${usage.turns > 1 ? 's' : ''}`);
  if (usage.input_tokens) parts.push(`↑${formatTokens(usage.input_tokens)}`);
  if (usage.output_tokens) parts.push(`↓${formatTokens(usage.output_tokens)}`);
  if (usage.cache_read) parts.push(`R${formatTokens(usage.cache_read)}`);
  if (usage.cache_write) parts.push(`W${formatTokens(usage.cache_write)}`);
  if (usage.cost) parts.push(`$${usage.cost.toFixed(4)}`);
  if (usage.context_tokens && usage.context_tokens > 0) {
    parts.push(`ctx:${formatTokens(usage.context_tokens)}`);
  }
  if (model) parts.push(model);
  return parts.join(' ');
}

function aggregateUsage(results: SubagentResultItem[]): SubagentUsage {
  const total: SubagentUsage = {};
  for (const r of results) {
    const u = r.usage ?? {};
    total.input_tokens = (total.input_tokens ?? 0) + (u.input_tokens ?? 0);
    total.output_tokens = (total.output_tokens ?? 0) + (u.output_tokens ?? 0);
    total.cache_read = (total.cache_read ?? 0) + (u.cache_read ?? 0);
    total.cache_write = (total.cache_write ?? 0) + (u.cache_write ?? 0);
    total.cost = (total.cost ?? 0) + (u.cost ?? 0);
    total.turns = (total.turns ?? 0) + (u.turns ?? 0);
  }
  return total;
}

function isFailed(r: SubagentResultItem): boolean {
  return (
    (r.exit_code !== undefined && r.exit_code !== 0 && r.exit_code !== -1) ||
    r.stop_reason === 'error' ||
    r.stop_reason === 'aborted' ||
    typeof r.error === 'string'
  );
}

function isRunning(r: SubagentResultItem): boolean {
  return r.exit_code === -1;
}

// ---------------------------------------------------------------------------
// 子会话消息读取（camelCase wire 域）
// ---------------------------------------------------------------------------

type DisplayItem =
  | { type: 'text'; text: string }
  | { type: 'toolCall'; name: string; args: Record<string, unknown> };

function getDisplayItems(messages: unknown[]): DisplayItem[] {
  const items: DisplayItem[] = [];
  for (const raw of messages) {
    const msg = raw as { role?: string; content?: unknown };
    if (msg?.role !== 'assistant' || !Array.isArray(msg.content)) continue;
    for (const part of msg.content as Array<Record<string, unknown>>) {
      if (part?.type === 'text' && typeof part.text === 'string') {
        items.push({ type: 'text', text: part.text });
      } else if (part?.type === 'toolCall') {
        items.push({
          type: 'toolCall',
          name: typeof part.name === 'string' ? part.name : '?',
          args: (part.arguments as Record<string, unknown>) ?? {},
        });
      }
    }
  }
  return items;
}

function getFinalOutput(messages: unknown[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i] as { role?: string; content?: unknown };
    if (msg?.role !== 'assistant' || !Array.isArray(msg.content)) continue;
    for (const part of msg.content as Array<Record<string, unknown>>) {
      if (part?.type === 'text' && typeof part.text === 'string') return part.text;
    }
  }
  return '';
}

// ---------------------------------------------------------------------------
// 渲染器
// ---------------------------------------------------------------------------

export default function renderSubagent(input: RendererInput): Component {
  const d = detailsOf(input) as SubagentDetails;
  const colors = input.env?.colors;
  const expanded = input.env?.expanded === true;
  const markdownTheme = input.env?.markdownTheme as MarkdownTheme | undefined;

  const dim = (s: string) => colors?.dim?.(s) ?? s;
  const muted = (s: string) => colors?.muted?.(s) ?? s;
  const accent = (s: string) => colors?.accent?.(s) ?? s;
  const ok = (s: string) => colors?.success?.(s) ?? s;
  const bad = (s: string) => colors?.error?.(s) ?? s;
  const warn = (s: string) => colors?.warning?.(s) ?? s;
  const toolOut = (s: string) => colors?.toolOutput?.(s) ?? s;
  const title = (s: string) => colors?.toolTitle?.(s) ?? s;

  /** 工具调用行格式化（pi formatToolCall 对位；nova 工具参数主键为 path）。 */
  function formatToolCall(name: string, args: Record<string, unknown>): string {
    const rawPath = (args.path ?? args.file_path ?? args.command ?? '') as string;
    switch (name) {
      case 'bash': {
        const command = (args.command as string) || '...';
        const preview = command.length > 60 ? `${command.slice(0, 60)}...` : command;
        return muted('$ ') + toolOut(preview);
      }
      case 'read': {
        let text = accent(rawPath || '...');
        const offset = args.offset as number | undefined;
        const limit = args.limit as number | undefined;
        if (offset !== undefined || limit !== undefined) {
          const start = offset ?? 1;
          const end = limit !== undefined ? start + limit - 1 : '';
          text += warn(`:${start}${end ? `-${end}` : ''}`);
        }
        return muted('read ') + text;
      }
      case 'write': {
        const content = (args.content as string) ?? '';
        const lines = content ? content.split('\n').length : 0;
        let text = muted('write ') + accent(rawPath || '...');
        if (lines > 1) text += dim(` (${lines} lines)`);
        return text;
      }
      case 'edit':
        return muted('edit ') + accent(rawPath || '...');
      case 'ls':
        return muted('ls ') + accent((args.path as string) || '.');
      case 'find':
        return (
          muted('find ') +
          accent((args.pattern as string) || '*') +
          dim(` in ${(args.path as string) || '.'}`)
        );
      case 'grep':
        return (
          muted('grep ') +
          accent(`/${(args.pattern as string) || ''}/`) +
          dim(` in ${(args.path as string) || '.'}`)
        );
      case 'todo':
        return muted('todo ') + accent('update');
      default: {
        const argsStr = JSON.stringify(args);
        const preview = argsStr.length > 50 ? `${argsStr.slice(0, 50)}...` : argsStr;
        return accent(name) + dim(` ${preview}`);
      }
    }
  }

  /** 展示项列表（末 N 条；文本折叠态只取前 3 行预览）。 */
  function renderDisplayItems(items: DisplayItem[], limit?: number): string {
    const toShow = limit ? items.slice(-limit) : items;
    const skipped = limit && items.length > limit ? items.length - limit : 0;
    let text = '';
    if (skipped > 0) text += muted(`... ${skipped} earlier items\n`);
    for (const item of toShow) {
      if (item.type === 'text') {
        const preview = expanded ? item.text : item.text.split('\n').slice(0, 3).join('\n');
        text += `${toolOut(preview)}\n`;
      } else {
        text += `${muted('→ ') + formatToolCall(item.name, item.args)}\n`;
      }
    }
    return text.trimEnd();
  }

  function statusIcon(r: SubagentResultItem): string {
    if (isRunning(r)) return warn('⏳');
    return isFailed(r) ? bad('✗') : ok('✓');
  }

  /** 单个结果的完整展开视图（task + 工具调用 + Markdown 终输出 + usage）。 */
  function renderExpandedResult(container: Container, r: SubagentResultItem): void {
    const displayItems = getDisplayItems(r.messages ?? []);
    const finalOutput = getFinalOutput(r.messages ?? []);
    container.addChild(new Text(`${muted('─── Task ───')}`, 1, 0));
    container.addChild(new Text(dim(r.task ?? ''), 1, 0));
    container.addChild(new Spacer(1));
    container.addChild(new Text(`${muted('─── Output ───')}`, 1, 0));
    if (displayItems.length === 0 && !finalOutput) {
      container.addChild(new Text(muted('(no output)'), 1, 0));
    } else {
      for (const item of displayItems) {
        if (item.type === 'toolCall') {
          container.addChild(new Text(muted('→ ') + formatToolCall(item.name, item.args), 1, 0));
        }
      }
      if (finalOutput) {
        container.addChild(new Spacer(1));
        if (markdownTheme) {
          container.addChild(new Markdown(finalOutput.trim(), 1, 0, markdownTheme));
        } else {
          container.addChild(new Text(finalOutput.trim(), 1, 0));
        }
      }
    }
    const usageStr = formatUsageStats(r.usage ?? {}, r.model);
    if (usageStr) {
      container.addChild(new Spacer(1));
      container.addChild(new Text(dim(usageStr), 1, 0));
    }
  }

  /** 调用头部（streaming 态或结果缺失时的标题——pi renderCall 对位）。 */
  function renderHeader(): Text {
    const args = (input.args ?? {}) as {
      agent?: string;
      task?: string;
      tasks?: Array<{ agent?: string; task?: string }>;
      chain?: Array<{ agent?: string; task?: string }>;
    };
    if (args.chain && args.chain.length > 0) {
      let text = title('subagent ') + accent(`chain (${args.chain.length} steps)`);
      for (let i = 0; i < Math.min(args.chain.length, 3); i++) {
        const step = args.chain[i];
        const clean = (step.task ?? '').replace(/\{previous\}/g, '').trim();
        const preview = clean.length > 40 ? `${clean.slice(0, 40)}...` : clean;
        text += `\n  ${muted(`${i + 1}.`)} ${accent(step.agent ?? '?')}${dim(` ${preview}`)}`;
      }
      if (args.chain.length > 3) text += `\n  ${muted(`... +${args.chain.length - 3} more`)}`;
      return new Text(text, 1, 0);
    }
    if (args.tasks && args.tasks.length > 0) {
      let text = title('subagent ') + accent(`parallel (${args.tasks.length} tasks)`);
      for (const t of args.tasks.slice(0, 3)) {
        const preview = (t.task ?? '').length > 40 ? `${(t.task ?? '').slice(0, 40)}...` : (t.task ?? '');
        text += `\n  ${accent(t.agent ?? '?')}${dim(` ${preview}`)}`;
      }
      if (args.tasks.length > 3) text += `\n  ${muted(`... +${args.tasks.length - 3} more`)}`;
      return new Text(text, 1, 0);
    }
    const preview = (args.task ?? '').length > 60 ? `${(args.task ?? '').slice(0, 60)}...` : (args.task ?? '');
    let text = title('subagent ') + accent(args.agent ?? '...');
    if (preview) text += `\n  ${dim(preview)}`;
    return new Text(text, 1, 0);
  }

  return new (class extends Container {
    override render(width: number): string[] {
      const container = new Container();
      const results = d.results ?? [];

      // 无结果（streaming/参数阶段或异常回执）：头部 + 内容文本兜底。
      if (results.length === 0) {
        container.addChild(renderHeader());
        if (input.status === 'running') {
          container.addChild(new Text(dim('running…'), 1, 0));
        }
        return container.render(width);
      }

      const mode = d.mode ?? 'single';

      // ---- single ----
      if (mode === 'single' && results.length === 1) {
        const r = results[0];
        const failed = isFailed(r);
        let header = `${statusIcon(r)} ${title(r.agent ?? '?')}`;
        if (r.agent_source) header += muted(` (${r.agent_source})`);
        if (failed && r.stop_reason) header += ` ${bad(`[${r.stop_reason}]`)}`;
        container.addChild(new Text(header, 1, 0));

        if (failed && (r.error_message || r.error)) {
          container.addChild(new Text(bad(`Error: ${r.error_message ?? r.error}`), 1, 0));
        }

        if (expanded) {
          container.addChild(new Spacer(1));
          renderExpandedResult(container, r);
        } else {
          const displayItems = getDisplayItems(r.messages ?? []);
          if (displayItems.length === 0 && !isRunning(r)) {
            if (!failed) container.addChild(new Text(muted('(no output)'), 1, 0));
          } else if (displayItems.length > 0) {
            container.addChild(new Text(renderDisplayItems(displayItems, COLLAPSED_ITEM_COUNT), 1, 0));
            if (displayItems.length > COLLAPSED_ITEM_COUNT) {
              container.addChild(new Text(muted('(ctrl+o to expand)'), 1, 0));
            }
          }
          if (!isRunning(r)) {
            const usageStr = formatUsageStats(r.usage ?? {}, r.model);
            if (usageStr) container.addChild(new Text(dim(usageStr), 1, 0));
          }
        }
        return container.render(width);
      }

      // ---- chain ----
      if (mode === 'chain') {
        const doneCount = results.filter((r) => !isRunning(r) && !isFailed(r)).length;
        const failCount = results.filter((r) => isFailed(r)).length;
        const icon = failCount > 0 ? bad('✗') : ok('✓');
        container.addChild(
          new Text(`${icon} ${title('chain ')}${accent(`${doneCount}/${results.length} steps`)}`, 1, 0),
        );
        results.forEach((r, index) => {
          container.addChild(new Spacer(1));
          container.addChild(
            new Text(`${muted(`─── Step ${index + 1}: `)}${accent(r.agent ?? '?')} ${statusIcon(r)}`, 1, 0),
          );
          if (expanded) {
            renderExpandedResult(container, r);
          } else {
            const displayItems = getDisplayItems(r.messages ?? []);
            if (displayItems.length === 0) {
              container.addChild(new Text(muted(isRunning(r) ? '(running...)' : '(no output)'), 1, 0));
            } else {
              container.addChild(new Text(renderDisplayItems(displayItems, PER_STEP_COLLAPSED), 1, 0));
            }
          }
        });
        const totalUsage = formatUsageStats(aggregateUsage(results));
        if (totalUsage && !results.some(isRunning)) {
          container.addChild(new Spacer(1));
          container.addChild(new Text(dim(`Total: ${totalUsage}`), 1, 0));
        }
        if (!expanded) container.addChild(new Text(muted('(ctrl+o to expand)'), 1, 0));
        return container.render(width);
      }

      // ---- parallel ----
      const running = results.filter(isRunning).length;
      const failedCount = results.filter((r) => !isRunning(r) && isFailed(r)).length;
      const doneCount = results.length - running;
      const successCount = doneCount - failedCount;
      const isLive = running > 0 && input.status !== 'done';
      const icon = isLive ? warn('⏳') : failedCount > 0 ? warn('◐') : ok('✓');
      const status = isLive
        ? `${doneCount}/${results.length} done, ${running} running`
        : `${successCount}/${results.length} tasks`;
      container.addChild(new Text(`${icon} ${title('parallel ')}${accent(status)}`, 1, 0));

      for (const r of results) {
        container.addChild(new Spacer(1));
        container.addChild(
          new Text(`${muted('─── ')}${accent(r.agent ?? '?')} ${statusIcon(r)}`, 1, 0),
        );
        if (expanded && !isLive) {
          renderExpandedResult(container, r);
        } else {
          const displayItems = getDisplayItems(r.messages ?? []);
          if (displayItems.length === 0) {
            container.addChild(new Text(muted(isRunning(r) ? '(running...)' : '(no output)'), 1, 0));
          } else {
            container.addChild(new Text(renderDisplayItems(displayItems, PER_STEP_COLLAPSED), 1, 0));
          }
        }
      }
      if (!isLive) {
        const totalUsage = formatUsageStats(aggregateUsage(results));
        if (totalUsage) {
          container.addChild(new Spacer(1));
          container.addChild(new Text(dim(`Total: ${totalUsage}`), 1, 0));
        }
      }
      if (!expanded) container.addChild(new Text(muted('(ctrl+o to expand)'), 1, 0));
      return container.render(width);
    }
  })();
}
