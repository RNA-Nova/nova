/**
 * question 工具渲染器（组件形态——pi question.ts 渲染语义对位；多问形态对位
 * pi questionnaire）。
 *
 * details 契约（backend/tools/question.py）：
 * - 单问：{ question, options: [label...], answer, was_custom, index? }；
 * - 多问：{ questions: [{question, options: [label...], answer, was_custom,
 *   index?}...] }（按 questions 是否存在分派）；
 * 取消 answer=null；参数/环境失败带 error。
 *
 * 呈现语义（pi 对齐）：
 * - 调用/等待中：问题 + 编号选项列表（含 "Type something." 自由项）+ 等待提示；
 *   多问逐问显示，当前问（首个未答）高亮并带选项列表，已答问显示 ✓ 回执；
 * - 取消：warning "Cancelled"；自由回答：✓ (wrote) 答案；选择：✓ N. label。
 */
import { Container, Text, type Component } from '@earendil-works/pi-tui';

import { detailsOf, type RendererInput } from 'nova-tui';

interface QuestionOption {
  label?: string;
  description?: string;
}

/** 渲染配色助手集（恒等函数兜底——无 env.colors 时不染色）。 */
interface ColorFns {
  dim: (s: string) => string;
  muted: (s: string) => string;
  accent: (s: string) => string;
  ok: (s: string) => string;
  warn: (s: string) => string;
  bad: (s: string) => string;
  title: (s: string) => string;
}

/** options 归一为 label 数组（details 侧为字符串数组；args 侧为对象数组）。 */
function normalizeLabels(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((o) => {
      if (typeof o === 'string') return o;
      if (typeof o === 'object' && o !== null) {
        const label = (o as QuestionOption).label;
        if (typeof label === 'string') return label;
      }
      return '';
    })
    .filter((s) => s.length > 0);
}

/** ✓ 回执行（单问/多问共用）：自由回答 ✓ (wrote) 答案；选择 ✓ N. label。 */
function answeredLine(
  answer: unknown,
  wasCustom: boolean,
  index: number | undefined,
  labels: string[],
  c: ColorFns,
): Text {
  if (wasCustom) {
    return new Text(c.ok('✓ ') + c.muted('(wrote) ') + c.accent(String(answer)), 1, 0);
  }
  const resolved = index ?? labels.indexOf(String(answer)) + 1;
  const display = resolved > 0 ? `${resolved}. ${answer}` : String(answer);
  return new Text(c.ok('✓ ') + c.accent(display), 1, 0);
}

/** 多问渲染（details.questions 优先，args.questions 兜底——参数流式阶段可读）。 */
function renderMultiQuestion(
  input: RendererInput,
  d: Record<string, unknown>,
  args: Record<string, unknown>,
  c: ColorFns,
  container: Container,
): Component {
  const rawEntries = (Array.isArray(d.questions) && d.questions.length > 0
    ? d.questions
    : args.questions) as unknown[];
  const entries = (Array.isArray(rawEntries) ? rawEntries : []).filter(
    (e): e is Record<string, unknown> => typeof e === 'object' && e !== null,
  );
  const norm = entries.map((e) => ({
    question: typeof e.question === 'string' ? e.question : '',
    labels: normalizeLabels(e.options),
    answer: e.answer,
    wasCustom: e.was_custom === true,
    index: typeof e.index === 'number' ? (e.index as number) : undefined,
  }));
  const isAnswered = (e: (typeof norm)[number]) => e.answer !== null && e.answer !== undefined;
  const answeredCount = norm.filter(isAnswered).length;
  const currentIndex = norm.findIndex((e) => !isAnswered(e));
  const terminal = input.status === 'done' || input.status === 'error';

  norm.forEach((entry, i) => {
    const current = !terminal && i === currentIndex;
    container.addChild(
      current
        ? new Text(c.accent(`question › ${entry.question}`), 1, 0)
        : new Text(c.title('question ') + c.muted(entry.question), 1, 0),
    );
    if (isAnswered(entry)) {
      container.addChild(answeredLine(entry.answer, entry.wasCustom, entry.index, entry.labels, c));
    } else if (current && entry.labels.length > 0) {
      const numbered = [...entry.labels, 'Type something.'].map((o, idx) => `${idx + 1}. ${o}`);
      container.addChild(new Text(c.dim(`  Options: ${numbered.join(', ')}`), 1, 0));
    }
  });

  if (input.status === 'running') {
    container.addChild(new Text(c.dim(`waiting for answer… (${answeredCount}/${norm.length})`), 1, 0));
  } else if (terminal && answeredCount < norm.length) {
    // 全部或部分未答完结（用户 Esc 取消）——pi 的 Cancelled 语义
    container.addChild(new Text(c.warn('Cancelled'), 1, 0));
  }
  return container;
}

export default function renderQuestion(input: RendererInput): Component {
  const d = detailsOf(input);
  const colors = input.env?.colors;
  const c: ColorFns = {
    dim: (s) => colors?.dim?.(s) ?? s,
    muted: (s) => colors?.muted?.(s) ?? s,
    accent: (s) => colors?.accent?.(s) ?? s,
    ok: (s) => colors?.success?.(s) ?? s,
    warn: (s) => colors?.warning?.(s) ?? s,
    bad: (s) => colors?.error?.(s) ?? s,
    title: (s) => colors?.toolTitle?.(s) ?? s,
  };

  const container = new Container();

  if (typeof d.error === 'string' && d.error) {
    container.addChild(new Text(c.bad(d.error), 1, 0));
    return container;
  }

  // 多问形态分派（details.questions / args.questions 非空数组）
  const args = (input.args ?? {}) as Record<string, unknown> & {
    question?: string;
    options?: QuestionOption[];
    questions?: unknown;
  };
  if (
    (Array.isArray(d.questions) && d.questions.length > 0) ||
    (Array.isArray(args.questions) && args.questions.length > 0)
  ) {
    return renderMultiQuestion(input, d, args, c, container);
  }

  // 问题 + 选项（args 在调用/等待阶段可读；完结后读 details 的 labels 兜底）
  const question = (typeof d.question === 'string' && d.question) || args.question || '';
  const labels: string[] = Array.isArray(d.options)
    ? (d.options as string[])
    : normalizeLabels(args.options);

  if (question) {
    container.addChild(new Text(c.title('question ') + c.muted(question), 1, 0));
  }
  if (labels.length > 0 && (input.status === 'streaming' || input.status === 'running')) {
    const numbered = [...labels, 'Type something.'].map((o, i) => `${i + 1}. ${o}`);
    container.addChild(new Text(c.dim(`  Options: ${numbered.join(', ')}`), 1, 0));
  }

  if (input.status === 'running') {
    container.addChild(new Text(c.dim('waiting for answer…'), 1, 0));
    return container;
  }
  if (input.status === 'streaming' || input.status === 'done' || input.status === 'error') {
    if (d.answer === undefined && input.status !== 'done' && input.status !== 'error') {
      return container; // 参数流式中，暂无结果语义
    }
    if (d.answer === null || d.answer === undefined) {
      if (input.status === 'done' || input.status === 'error') {
        container.addChild(new Text(c.warn('Cancelled'), 1, 0));
      }
      return container;
    }
    if (d.was_custom === true) {
      container.addChild(
        new Text(c.ok('✓ ') + c.muted('(wrote) ') + c.accent(String(d.answer)), 1, 0),
      );
      return container;
    }
    const index = typeof d.index === 'number' ? d.index : labels.indexOf(String(d.answer)) + 1;
    const display = index > 0 ? `${index}. ${d.answer}` : String(d.answer);
    container.addChild(new Text(c.ok('✓ ') + c.accent(display), 1, 0));
  }
  return container;
}
