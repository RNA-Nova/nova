/**
 * edit 执行前预览计算。
 *
 * 用途：参数完整、执行未开始（argsComplete）时，用完整参数只读地跑一遍
 * 匹配逻辑并生成 unified patch——用户在工具执行前就看到要改什么，
 * 匹配失败也提前看到错误。
 *
 * 与执行端（tools/edit.py + tools_common/operations.py，Python）的关系：
 * 两侧是同一匹配语义的双实现——跨语言边界共享只能靠契约一致
 * （多级匹配：精确 → fuzzy 归一；多 edit 反向应用；fuzzy 时保留未改行
 * 原始字节）。
 */

import * as Diff from './vendor/jsdiff.cjs';
import { constants } from 'node:fs';
import { access, readFile } from 'node:fs/promises';
import { isAbsolute, resolve } from 'node:path';

// ---------------------------------------------------------------------------
// 换行与 BOM 处理
// ---------------------------------------------------------------------------

export function normalizeToLF(text: string): string {
  return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
}

/** 剥离 UTF-8 BOM（LLM 不会在 oldText 里包含不可见 BOM）。 */
export function stripBom(content: string): { bom: string; text: string } {
  return content.startsWith('\uFEFF')
    ? { bom: '\uFEFF', text: content.slice(1) }
    : { bom: '', text: content };
}

/**
 * fuzzy 匹配归一（渐进变换）：
 * - 行尾空白剔除
 * - 弯引号 → 直引号
 * - Unicode 破折号/连字符 → ASCII '-'
 * - 特殊 Unicode 空格 → 普通空格
 */
export function normalizeForFuzzyMatch(text: string): string {
  return (
    text
      .normalize('NFKC')
      .split('\n')
      .map((line) => line.trimEnd())
      .join('\n')
      // 弯单引号 → '
      .replace(/[\u2018\u2019\u201A\u201B]/g, "'")
      // 弯双引号 → "
      .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
      // 各类破折号/连字符 → -
      .replace(/[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]/g, '-')
      // 特殊空格 → 普通空格
      .replace(/[\u00A0\u2002-\u200A\u202F\u205F\u3000]/g, ' ')
  );
}

// ---------------------------------------------------------------------------
// 行结构工具
// ---------------------------------------------------------------------------

function splitLinesWithEndings(content: string): string[] {
  return content.match(/[^\n]*\n|[^\n]+/g) ?? [];
}

interface LineSpan {
  start: number;
  end: number;
}

interface MatchedEdit {
  editIndex: number;
  matchIndex: number;
  matchLength: number;
  newText: string;
}

type TextReplacement = Pick<MatchedEdit, 'matchIndex' | 'matchLength' | 'newText'>;

function getLineSpans(content: string): LineSpan[] {
  let offset = 0;
  return splitLinesWithEndings(content).map((line) => {
    const span = { start: offset, end: offset + line.length };
    offset = span.end;
    return span;
  });
}

function getReplacementLineRange(lines: LineSpan[], replacement: TextReplacement) {
  const replacementStart = replacement.matchIndex;
  const replacementEnd = replacement.matchIndex + replacement.matchLength;

  let startLine = -1;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    if (replacementStart >= line.start && replacementStart < line.end) {
      startLine = i;
      break;
    }
  }
  if (startLine === -1) {
    throw new Error('Replacement range is outside the base content.');
  }

  let endLine = startLine;
  while (endLine < lines.length && lines[endLine]!.end < replacementEnd) {
    endLine++;
  }
  if (endLine >= lines.length) {
    throw new Error('Replacement range is outside the base content.');
  }

  return { startLine, endLine: endLine + 1 };
}

/** 反向应用替换（后应用的替换不影响先应用的偏移）。 */
function applyReplacements(content: string, replacements: TextReplacement[], offset = 0): string {
  let result = content;
  for (let i = replacements.length - 1; i >= 0; i--) {
    const replacement = replacements[i]!;
    const matchIndex = replacement.matchIndex - offset;
    result =
      result.substring(0, matchIndex) +
      replacement.newText +
      result.substring(matchIndex + replacement.matchLength);
  }
  return result;
}

/**
 * 把针对 baseContent（归一视图）匹配的替换应用到 originalContent，
 * 未触及的行保留原始字节（fuzzy 匹配时未改行的行尾空白/弯引号不被归一污染）。
 */
export function applyReplacementsPreservingUnchangedLines(
  originalContent: string,
  baseContent: string,
  replacements: TextReplacement[],
): string {
  const originalLines = splitLinesWithEndings(originalContent);
  const baseLines = getLineSpans(baseContent);
  if (originalLines.length !== baseLines.length) {
    throw new Error(
      'Cannot preserve unchanged lines because the base content has a different line count.',
    );
  }

  const groups: Array<{ startLine: number; endLine: number; replacements: TextReplacement[] }> = [];
  const sortedReplacements = [...replacements].sort((a, b) => a.matchIndex - b.matchIndex);
  for (const replacement of sortedReplacements) {
    const range = getReplacementLineRange(baseLines, replacement);
    const current = groups[groups.length - 1];
    if (current && range.startLine < current.endLine) {
      current.endLine = Math.max(current.endLine, range.endLine);
      current.replacements.push(replacement);
      continue;
    }
    groups.push({ ...range, replacements: [replacement] });
  }

  let originalLineIndex = 0;
  let result = '';
  for (const group of groups) {
    result += originalLines.slice(originalLineIndex, group.startLine).join('');
    const groupStartOffset = baseLines[group.startLine]!.start;
    const groupEndOffset = baseLines[group.endLine - 1]!.end;
    result += applyReplacements(
      baseContent.slice(groupStartOffset, groupEndOffset),
      group.replacements,
      groupStartOffset,
    );
    originalLineIndex = group.endLine;
  }
  result += originalLines.slice(originalLineIndex).join('');

  return result;
}

// ---------------------------------------------------------------------------
// 匹配（精确 → fuzzy 两级）
// ---------------------------------------------------------------------------

export interface FuzzyMatchResult {
  found: boolean;
  index: number;
  matchLength: number;
  usedFuzzyMatch: boolean;
  /** 替换应基于的内容（精确：原始内容；fuzzy：归一内容）。 */
  contentForReplacement: string;
}

export function fuzzyFindText(content: string, oldText: string): FuzzyMatchResult {
  const exactIndex = content.indexOf(oldText);
  if (exactIndex !== -1) {
    return {
      found: true,
      index: exactIndex,
      matchLength: oldText.length,
      usedFuzzyMatch: false,
      contentForReplacement: content,
    };
  }

  const fuzzyContent = normalizeForFuzzyMatch(content);
  const fuzzyOldText = normalizeForFuzzyMatch(oldText);
  const fuzzyIndex = fuzzyContent.indexOf(fuzzyOldText);

  if (fuzzyIndex === -1) {
    return {
      found: false,
      index: -1,
      matchLength: 0,
      usedFuzzyMatch: false,
      contentForReplacement: content,
    };
  }

  return {
    found: true,
    index: fuzzyIndex,
    matchLength: fuzzyOldText.length,
    usedFuzzyMatch: true,
    contentForReplacement: fuzzyContent,
  };
}

function countOccurrences(content: string, oldText: string): number {
  const fuzzyContent = normalizeForFuzzyMatch(content);
  const fuzzyOldText = normalizeForFuzzyMatch(oldText);
  return fuzzyContent.split(fuzzyOldText).length - 1;
}

// ---------------------------------------------------------------------------
// 编辑应用（匹配 → 校验 → 应用）
// ---------------------------------------------------------------------------

export interface PreviewEdit {
  oldText: string;
  newText: string;
}

function getNotFoundError(path: string, editIndex: number, totalEdits: number): Error {
  if (totalEdits === 1) {
    return new Error(
      `Could not find the exact text in ${path}. The old text must match exactly including all whitespace and newlines.`,
    );
  }
  return new Error(
    `Could not find edits[${editIndex}] in ${path}. The oldText must match exactly including all whitespace and newlines.`,
  );
}

function getDuplicateError(
  path: string,
  editIndex: number,
  totalEdits: number,
  occurrences: number,
): Error {
  if (totalEdits === 1) {
    return new Error(
      `Found ${occurrences} occurrences of the text in ${path}. The text must be unique. Please provide more context to make it unique.`,
    );
  }
  return new Error(
    `Found ${occurrences} occurrences of edits[${editIndex}] in ${path}. Each oldText must be unique. Please provide more context to make it unique.`,
  );
}

/** 对 LF 归一内容应用全部编辑（全部匹配同一原文；重叠/重复/未找到即抛错）。 */
export function applyEditsToNormalizedContent(
  normalizedContent: string,
  edits: PreviewEdit[],
  path: string,
): { baseContent: string; newContent: string } {
  const normalizedEdits = edits.map((edit) => ({
    oldText: normalizeToLF(edit.oldText),
    newText: normalizeToLF(edit.newText),
  }));

  for (let i = 0; i < normalizedEdits.length; i++) {
    if (normalizedEdits[i]!.oldText.length === 0) {
      throw new Error(`edits[${i}].oldText must not be empty in ${path}.`);
    }
  }

  const initialMatches = normalizedEdits.map((edit) =>
    fuzzyFindText(normalizedContent, edit.oldText),
  );
  const usedFuzzyMatch = initialMatches.some((match) => match.usedFuzzyMatch);
  const replacementBaseContent = usedFuzzyMatch
    ? normalizeForFuzzyMatch(normalizedContent)
    : normalizedContent;

  const matchedEdits: MatchedEdit[] = [];
  for (let i = 0; i < normalizedEdits.length; i++) {
    const edit = normalizedEdits[i]!;
    const matchResult = fuzzyFindText(replacementBaseContent, edit.oldText);
    if (!matchResult.found) {
      throw getNotFoundError(path, i, normalizedEdits.length);
    }

    const occurrences = countOccurrences(replacementBaseContent, edit.oldText);
    if (occurrences > 1) {
      throw getDuplicateError(path, i, normalizedEdits.length, occurrences);
    }

    matchedEdits.push({
      editIndex: i,
      matchIndex: matchResult.index,
      matchLength: matchResult.matchLength,
      newText: edit.newText,
    });
  }

  matchedEdits.sort((a, b) => a.matchIndex - b.matchIndex);
  for (let i = 1; i < matchedEdits.length; i++) {
    const previous = matchedEdits[i - 1]!;
    const current = matchedEdits[i]!;
    if (previous.matchIndex + previous.matchLength > current.matchIndex) {
      throw new Error(
        `edits[${previous.editIndex}] and edits[${current.editIndex}] overlap in ${path}. Merge them into one edit or target disjoint regions.`,
      );
    }
  }

  const baseContent = normalizedContent;
  const newContent = usedFuzzyMatch
    ? applyReplacementsPreservingUnchangedLines(
        normalizedContent,
        replacementBaseContent,
        matchedEdits,
      )
    : applyReplacements(replacementBaseContent, matchedEdits);

  if (baseContent === newContent) {
    throw new Error(`No changes made to ${path}. The replacement produced identical content.`);
  }

  return { baseContent, newContent };
}

// ---------------------------------------------------------------------------
// 预览计算（只读）
// ---------------------------------------------------------------------------

export interface EditPreviewPatch {
  /** 标准 unified patch（渲染器 parseUnifiedPatch 直接消费）。 */
  patch: string;
  path: string;
}

export interface EditPreviewError {
  error: string;
}

export type EditPreviewResult = EditPreviewPatch | EditPreviewError;

/**
 * 只读地计算一组编辑的 unified patch（不执行写盘）。
 * nova 渲染器契约为 patch 文本。
 */
export async function computeEditPreview(
  path: string,
  edits: PreviewEdit[],
  cwd: string,
): Promise<EditPreviewResult> {
  const absolutePath = isAbsolute(path) ? path : resolve(cwd, path);

  try {
    try {
      await access(absolutePath, constants.R_OK);
    } catch (error: unknown) {
      const errorMessage =
        error instanceof Error && 'code' in error
          ? `Error code: ${(error as { code?: unknown }).code}`
          : String(error);
      return { error: `Could not edit file: ${path}. ${errorMessage}.` };
    }

    const rawContent = await readFile(absolutePath, 'utf-8');
    const { text: content } = stripBom(rawContent);
    const normalizedContent = normalizeToLF(content);
    const { baseContent, newContent } = applyEditsToNormalizedContent(
      normalizedContent,
      edits,
      path,
    );

    const patch = Diff.createTwoFilesPatch(path, path, baseContent, newContent);
    return { patch, path };
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error) };
  }
}
