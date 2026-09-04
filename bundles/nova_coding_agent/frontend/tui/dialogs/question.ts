/**
 * question 自定义对话框组件（dialog:question
 * 单框组件：选项列表 + "Type something." 内联编辑器；多问形态为
 * tab 条分页的多问单框）。
 *
 * 契约（与 backend/tools/question.py 配对）：
 * - 单问入参 params：{ question: string, options: [{label, description?}] }；
 * - 多问入参 params：{ questions: [{question, options: [{label, description?}]}...] }
 *   （1~4 问，按 questions 是否存在分派）；
 * - 单问 done({answer, wasCustom, index?})：选项选择 wasCustom=false + index
 *   （1 起）；自由输入 wasCustom=true（无 index）；done(undefined) = 取消；
 * - 多问 done({answers: [{answer, wasCustom, index?}...]})：按问序归集，
 *   条目语义与单问一致；
 * - 值键名为 camel（wasCustom——TS 生产侧键随 wire 原样透传，与工具 details
 *   的 snake 惯例同源于"生产者原样"原则）。
 *
 * 单问键位：↑↓ 移动 · enter 选择（自由项进编辑态）· esc 编辑态返回/选项态取消。
 * 多问键位：↑↓ 移动 · enter 选择（答完全部问后提交）· ←→/tab/shift+tab 切页 ·
 * esc 编辑态返回/选项态取消。
 */
import {
  Editor,
  Key,
  matchesKey,
  visibleWidth,
  wrapTextWithAnsi,
  type Component,
  type EditorTheme,
  type Focusable,
  type TUI,
} from '@earendil-works/pi-tui';

import { colors } from 'nova-tui/modes/tui/themes/index';

export interface QuestionDialogOption {
  label: string;
  description?: string;
}

interface DisplayOption extends QuestionDialogOption {
  isOther?: boolean;
}

const OTHER_LABEL = 'Type something.';

/** 问题对话框（选项 + 内联自由输入）。 */
export class QuestionDialog implements Component, Focusable {
  private optionIndex = 0;
  private editMode = false;
  private readonly editor: Editor;
  private cachedWidth?: number;
  private cachedLines?: string[];
  private readonly allOptions: DisplayOption[];
  private _focused = false;

  constructor(
    tui: TUI,
    private readonly question: string,
    options: QuestionDialogOption[],
    private readonly onDone: (result?: { answer: string; wasCustom: boolean; index?: number }) => void,
  ) {
    this.allOptions = [...options, { label: OTHER_LABEL, isOther: true }];
    const editorTheme: EditorTheme = {
      borderColor: (s) => colors.accent(s),
      selectList: {
        selectedPrefix: (t) => colors.accent(t),
        selectedText: (t) => colors.accent(t),
        description: (t) => colors.muted(t),
        scrollInfo: (t) => colors.dim(t),
        noMatch: (t) => colors.warning(t),
      },
    };
    this.editor = new Editor(tui, editorTheme);
    this.editor.onSubmit = (value) => {
      const trimmed = value.trim();
      if (trimmed) {
        this.onDone({ answer: trimmed, wasCustom: true });
      } else {
        this.editMode = false;
        this.editor.setText('');
        this.refresh();
      }
    };
  }

  get focused(): boolean {
    return this._focused;
  }

  set focused(value: boolean) {
    this._focused = value;
  }

  private refresh(): void {
    this.cachedLines = undefined;
    this.cachedWidth = undefined;
  }

  invalidate(): void {
    this.refresh();
  }

  handleInput(data: string): void {
    if (this.editMode) {
      if (matchesKey(data, Key.escape)) {
        this.editMode = false;
        this.editor.setText('');
        this.refresh();
        return;
      }
      this.editor.handleInput(data);
      this.refresh();
      return;
    }

    if (matchesKey(data, Key.up)) {
      this.optionIndex = Math.max(0, this.optionIndex - 1);
      this.refresh();
      return;
    }
    if (matchesKey(data, Key.down)) {
      this.optionIndex = Math.min(this.allOptions.length - 1, this.optionIndex + 1);
      this.refresh();
      return;
    }
    if (matchesKey(data, Key.enter)) {
      const selected = this.allOptions[this.optionIndex];
      if (selected.isOther) {
        this.editMode = true;
        this.refresh();
      } else {
        this.onDone({ answer: selected.label, wasCustom: false, index: this.optionIndex + 1 });
      }
      return;
    }
    if (matchesKey(data, Key.escape)) {
      this.onDone(undefined);
    }
  }

  render(width: number): string[] {
    if (this.cachedLines && this.cachedWidth === width) return this.cachedLines;

    const lines: string[] = [];
    const renderWidth = Math.max(1, width);

    const addWrapped = (text: string) => {
      lines.push(...wrapTextWithAnsi(text, renderWidth));
    };
    const addWrappedWithPrefix = (prefix: string, text: string) => {
      const prefixWidth = visibleWidth(prefix);
      if (prefixWidth >= renderWidth) {
        addWrapped(prefix + text);
        return;
      }
      const wrapped = wrapTextWithAnsi(text, renderWidth - prefixWidth);
      const continuationPrefix = ' '.repeat(prefixWidth);
      for (let i = 0; i < wrapped.length; i++) {
        lines.push(`${i === 0 ? prefix : continuationPrefix}${wrapped[i]}`);
      }
    };

    lines.push(colors.accent('─'.repeat(renderWidth)));
    addWrappedWithPrefix(' ', this.question);
    lines.push('');

    for (let i = 0; i < this.allOptions.length; i++) {
      const opt = this.allOptions[i];
      const selected = i === this.optionIndex;
      const isOther = opt.isOther === true;
      const prefix = selected ? colors.accent('> ') : '  ';
      const label = `${i + 1}. ${opt.label}${isOther && this.editMode ? ' ✎' : ''}`;
      const styled = selected || (isOther && this.editMode) ? colors.accent(label) : label;
      addWrappedWithPrefix(prefix, styled);
      if (opt.description) {
        addWrappedWithPrefix('     ', colors.muted(opt.description));
      }
    }

    if (this.editMode) {
      lines.push('');
      addWrappedWithPrefix(' ', colors.muted('Your answer:'));
      for (const line of this.editor.render(Math.max(1, renderWidth - 2))) {
        lines.push(` ${line}`);
      }
    }

    lines.push('');
    if (this.editMode) {
      addWrappedWithPrefix(' ', colors.dim('enter 提交 · esc 返回'));
    } else {
      addWrappedWithPrefix(' ', colors.dim('↑↓ 移动 · enter 选择 · esc 取消'));
    }
    lines.push(colors.accent('─'.repeat(renderWidth)));

    this.cachedWidth = width;
    this.cachedLines = lines;
    return lines;
  }
}

/** 多问入参的单问条目（options 语义与单问一致）。 */
export interface QuestionnaireQuestion {
  question: string;
  options: QuestionDialogOption[];
}

/** 多问应答的单问条目（与单问 {answer, wasCustom, index?} 同形状）。 */
export interface QuestionnaireAnswer {
  answer: string;
  wasCustom: boolean;
  index?: number;
}

/** 每页交互状态（选项光标 / 编辑态 / 页内编辑器——页间独立保留）。 */
interface PageState {
  optionIndex: number;
  editMode: boolean;
  editor: Editor;
}

const QUESTION_EDITOR_THEME: EditorTheme = {
  borderColor: (s) => colors.accent(s),
  selectList: {
    selectedPrefix: (t) => colors.accent(t),
    selectedText: (t) => colors.accent(t),
    description: (t) => colors.muted(t),
    scrollInfo: (t) => colors.dim(t),
    noMatch: (t) => colors.warning(t),
  },
};

/**
 * 多问对话框：顶部 tab 条显示问 1..N（已答 ✓ 标记），
 * 每页复用单问页（选项列表 + "Type something." 内联编辑器）。答完一问自动
 * 跳到下一未答问；全部答完后 enter 提交 {answers: [...]}（按问序归集）。
 */
export class QuestionnaireDialog implements Component, Focusable {
  private currentPage = 0;
  private readonly answers: Array<QuestionnaireAnswer | undefined>;
  private readonly pages: PageState[];
  private cachedWidth?: number;
  private cachedLines?: string[];
  private _focused = false;

  constructor(
    tui: TUI,
    private readonly questions: QuestionnaireQuestion[],
    private readonly onDone: (result?: { answers: QuestionnaireAnswer[] }) => void,
  ) {
    this.answers = questions.map(() => undefined);
    this.pages = questions.map(() => ({
      optionIndex: 0,
      editMode: false,
      editor: new Editor(tui, QUESTION_EDITOR_THEME),
    }));
    this.pages.forEach((page, i) => {
      page.editor.onSubmit = (value) => {
        const trimmed = value.trim();
        if (trimmed) {
          this.recordAnswer(i, { answer: trimmed, wasCustom: true });
        } else {
          page.editMode = false;
          page.editor.setText('');
          this.refresh();
        }
      };
    });
  }

  get focused(): boolean {
    return this._focused;
  }

  set focused(value: boolean) {
    this._focused = value;
  }

  private get allAnswered(): boolean {
    return this.answers.every((a) => a !== undefined);
  }

  private pageOptions(index: number): DisplayOption[] {
    return [...this.questions[index].options, { label: OTHER_LABEL, isOther: true }];
  }

  private refresh(): void {
    this.cachedLines = undefined;
    this.cachedWidth = undefined;
  }

  invalidate(): void {
    this.refresh();
  }

  /** 记录当前页答案：未答完跳下一未答问；全答完停留本页（下一次 enter 提交）。 */
  private recordAnswer(pageIndex: number, answer: QuestionnaireAnswer): void {
    this.answers[pageIndex] = answer;
    const page = this.pages[pageIndex];
    page.editMode = false;
    page.editor.setText('');
    if (!this.allAnswered) {
      this.currentPage = this.nextUnanswered(pageIndex);
      this.syncCursorToAnswer();
    }
    this.refresh();
  }

  /** 循环找下一未答问（from 之后起算；全部已答不会调用，兜底回 from）。 */
  private nextUnanswered(from: number): number {
    for (let step = 1; step <= this.questions.length; step++) {
      const index = (from + step) % this.questions.length;
      if (this.answers[index] === undefined) return index;
    }
    return from;
  }

  /** ←→/tab 切页（循环）。 */
  private switchPage(delta: number): void {
    const n = this.questions.length;
    if (n < 2) return;
    this.currentPage = (this.currentPage + delta + n) % n;
    this.syncCursorToAnswer();
    this.refresh();
  }

  /** 光标落到本页已答项（选项答归位编号项；自由答落 "Type something."）。 */
  private syncCursorToAnswer(): void {
    const answer = this.answers[this.currentPage];
    if (!answer) return;
    const page = this.pages[this.currentPage];
    page.optionIndex =
      answer.index !== undefined
        ? Math.max(0, answer.index - 1)
        : this.pageOptions(this.currentPage).length - 1;
  }

  private submit(): void {
    if (!this.allAnswered) return; // 未答完不提交（双保险）
    this.onDone({ answers: this.answers.map((a) => ({ ...a }) as QuestionnaireAnswer) });
  }

  handleInput(data: string): void {
    const page = this.pages[this.currentPage];

    if (page.editMode) {
      if (matchesKey(data, Key.escape)) {
        page.editMode = false;
        page.editor.setText('');
        this.refresh();
        return;
      }
      page.editor.handleInput(data);
      this.refresh();
      return;
    }

    if (matchesKey(data, Key.up)) {
      page.optionIndex = Math.max(0, page.optionIndex - 1);
      this.refresh();
      return;
    }
    if (matchesKey(data, Key.down)) {
      page.optionIndex = Math.min(this.pageOptions(this.currentPage).length - 1, page.optionIndex + 1);
      this.refresh();
      return;
    }
    if (matchesKey(data, Key.left) || matchesKey(data, 'shift+tab')) {
      this.switchPage(-1);
      return;
    }
    if (matchesKey(data, Key.right) || matchesKey(data, Key.tab)) {
      this.switchPage(1);
      return;
    }
    if (matchesKey(data, Key.enter)) {
      if (this.allAnswered) {
        this.submit();
        return;
      }
      const selected = this.pageOptions(this.currentPage)[page.optionIndex];
      if (selected.isOther) {
        page.editMode = true;
        this.refresh();
      } else {
        this.recordAnswer(this.currentPage, {
          answer: selected.label,
          wasCustom: false,
          index: page.optionIndex + 1,
        });
      }
      return;
    }
    if (matchesKey(data, Key.escape)) {
      this.onDone(undefined);
    }
  }

  render(width: number): string[] {
    if (this.cachedLines && this.cachedWidth === width) return this.cachedLines;

    const lines: string[] = [];
    const renderWidth = Math.max(1, width);

    const addWrapped = (text: string) => {
      lines.push(...wrapTextWithAnsi(text, renderWidth));
    };
    const addWrappedWithPrefix = (prefix: string, text: string) => {
      const prefixWidth = visibleWidth(prefix);
      if (prefixWidth >= renderWidth) {
        addWrapped(prefix + text);
        return;
      }
      const wrapped = wrapTextWithAnsi(text, renderWidth - prefixWidth);
      const continuationPrefix = ' '.repeat(prefixWidth);
      for (let i = 0; i < wrapped.length; i++) {
        lines.push(`${i === 0 ? prefix : continuationPrefix}${wrapped[i]}`);
      }
    };

    lines.push(colors.accent('─'.repeat(renderWidth)));

    // tab 条：问 1..N，已答 ✓ 标记，当前页高亮
    const tabs = this.questions.map((_, i) => {
      const answered = this.answers[i] !== undefined;
      const tab = `[${i + 1}${answered ? '✓' : ''}]`;
      return i === this.currentPage ? colors.accent(tab) : answered ? tab : colors.muted(tab);
    });
    addWrappedWithPrefix(' ', tabs.join(' '));
    lines.push('');

    // 当前页 = 单问页（问题 + 选项 + 内联编辑器）
    const page = this.pages[this.currentPage];
    addWrappedWithPrefix(' ', this.questions[this.currentPage].question);
    lines.push('');

    const options = this.pageOptions(this.currentPage);
    for (let i = 0; i < options.length; i++) {
      const opt = options[i];
      const selected = i === page.optionIndex;
      const isOther = opt.isOther === true;
      const prefix = selected ? colors.accent('> ') : '  ';
      const label = `${i + 1}. ${opt.label}${isOther && page.editMode ? ' ✎' : ''}`;
      const styled = selected || (isOther && page.editMode) ? colors.accent(label) : label;
      addWrappedWithPrefix(prefix, styled);
      if (opt.description) {
        addWrappedWithPrefix('     ', colors.muted(opt.description));
      }
    }

    if (page.editMode) {
      lines.push('');
      addWrappedWithPrefix(' ', colors.muted('Your answer:'));
      for (const line of page.editor.render(Math.max(1, renderWidth - 2))) {
        lines.push(` ${line}`);
      }
    }

    lines.push('');
    if (page.editMode) {
      addWrappedWithPrefix(' ', colors.dim('enter 提交 · esc 返回'));
    } else if (this.allAnswered) {
      addWrappedWithPrefix(' ', colors.dim('←→/tab 切页 · enter 提交 · esc 取消'));
    } else {
      addWrappedWithPrefix(' ', colors.dim('↑↓ 移动 · enter 选择 · ←→/tab 切页 · esc 取消'));
    }
    lines.push(colors.accent('─'.repeat(renderWidth)));

    this.cachedWidth = width;
    this.cachedLines = lines;
    return lines;
  }
}

/** dialog:question 工厂（ExtensionUIAPI.registerDialog 的注册形态）。 */
export function questionDialogFactory(
  env: unknown,
  params: Record<string, unknown>,
  done: (result?: unknown) => void,
): Component {
  const { tui } = env as { tui: TUI };
  // 多问形态：questions 数组存在即分派（1~4 问）
  if (Array.isArray(params.questions)) {
    const questions = normalizeQuestions(params.questions);
    if (questions.length > 0) {
      return new QuestionnaireDialog(tui, questions, (result) => done(result));
    }
    // 空数组/全无效：落单问路径兜底
  }
  const question = typeof params.question === 'string' ? params.question : '';
  const options = normalizeOptions(params.options);
  return new QuestionDialog(tui, question, options, (result) => done(result));
}

/** 选项归一化（单问/多问共用）：非对象、无 label 项过滤。 */
function normalizeOptions(raw: unknown): QuestionDialogOption[] {
  const rawOptions = Array.isArray(raw) ? raw : [];
  return rawOptions
    .filter((o): o is Record<string, unknown> => typeof o === 'object' && o !== null)
    .map((o) => ({
      label: typeof o.label === 'string' ? o.label : '',
      description: typeof o.description === 'string' ? o.description : undefined,
    }))
    .filter((o) => o.label.length > 0);
}

const MAX_QUESTIONS = 4;

/** 多问入参归一化：有效问（对象 + 非空问题文本），截断到 4 问。 */
function normalizeQuestions(raw: unknown[]): QuestionnaireQuestion[] {
  return raw
    .filter((q): q is Record<string, unknown> => typeof q === 'object' && q !== null)
    .map((q) => ({
      question: typeof q.question === 'string' ? q.question : '',
      options: normalizeOptions(q.options),
    }))
    .filter((q) => q.question.length > 0)
    .slice(0, MAX_QUESTIONS);
}
