"""edit 工具的编辑引擎。

语义（逐项对齐）：

- 每个 ``oldText`` 必须在原文中**唯一**（出现多次 → 报错）；
- 全部 edit 针对**同一份原文**匹配（非顺序应用），区域重叠 → 报错；
- 找不到 / 空 oldText / 替换后内容无变化 → 整个调用报错（原子性）；
- **fuzzy 匹配兜底**：精确匹配失败时在归一化空间重试
  （NFKC、行尾空白剥离、智能引号/破折号/特殊空格归一为 ASCII）；
  fuzzy 命中时替换在归一化空间进行，再按"未触及的行保留原始字节"
  叠回原文（``apply_replacements_preserving_unchanged_lines``）。
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class Edit:
    old_text: str
    new_text: str


@dataclass
class AppliedEditsResult:
    base_content: str
    new_content: str


# ---------------------------------------------------------------------------
# 行尾与 BOM
# ---------------------------------------------------------------------------


def detect_line_ending(content: str) -> str:
    """检测文件换行符（以先出现者为准）。"""
    crlf_idx = content.find("\r\n")
    lf_idx = content.find("\n")
    if lf_idx == -1:
        return "\n"
    if crlf_idx == -1:
        return "\n"
    return "\r\n" if crlf_idx < lf_idx else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


def strip_bom(content: str) -> Tuple[str, str]:
    """剥离 UTF-8 BOM，返回 (bom, 内容)。"""
    if content.startswith("\ufeff"):
        return "\ufeff", content[1:]
    return "", content


# ---------------------------------------------------------------------------
# Fuzzy 匹配
# ---------------------------------------------------------------------------

# 智能单引号 → '；智能双引号 → "；各类破折号 → -；特殊空格 → 普通空格
_SMART_SQUOTE = re.compile(r"[\u2018\u2019\u201a\u201b]")
_SMART_DQUOTE = re.compile(r"[\u201c\u201d\u201e\u201f]")
_DASHES = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
_SPECIAL_SPACES = re.compile(r"[\u00a0\u2002-\u200a\u202f\u205f\u3000]")


def normalize_for_fuzzy_match(text: str) -> str:
    """fuzzy 匹配归一化。"""
    text = unicodedata.normalize("NFKC", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _SMART_SQUOTE.sub("'", text)
    text = _SMART_DQUOTE.sub('"', text)
    text = _DASHES.sub("-", text)
    text = _SPECIAL_SPACES.sub(" ", text)
    return text


@dataclass
class FuzzyMatchResult:
    found: bool
    index: int
    match_length: int
    used_fuzzy_match: bool
    content_for_replacement: str


def fuzzy_find_text(content: str, old_text: str) -> FuzzyMatchResult:
    """先精确匹配，失败则在归一化空间 fuzzy 匹配。"""
    exact_index = content.find(old_text)
    if exact_index != -1:
        return FuzzyMatchResult(
            found=True,
            index=exact_index,
            match_length=len(old_text),
            used_fuzzy_match=False,
            content_for_replacement=content,
        )

    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old_text)
    fuzzy_index = fuzzy_content.find(fuzzy_old)
    if fuzzy_index == -1:
        return FuzzyMatchResult(
            found=False,
            index=-1,
            match_length=0,
            used_fuzzy_match=False,
            content_for_replacement=content,
        )
    return FuzzyMatchResult(
        found=True,
        index=fuzzy_index,
        match_length=len(fuzzy_old),
        used_fuzzy_match=True,
        content_for_replacement=fuzzy_content,
    )


def _count_occurrences(content: str, old_text: str) -> int:
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old_text)
    return fuzzy_content.count(fuzzy_old)


# ---------------------------------------------------------------------------
# 替换应用
# ---------------------------------------------------------------------------


@dataclass
class _MatchedEdit:
    edit_index: int
    match_index: int
    match_length: int
    new_text: str


def _split_lines_with_endings(content: str) -> List[str]:
    """按行切分并保留行尾。"""
    return re.findall(r"[^\n]*\n|[^\n]+", content)


def _get_line_spans(content: str) -> List[Tuple[int, int]]:
    offset = 0
    spans: List[Tuple[int, int]] = []
    for line in _split_lines_with_endings(content):
        spans.append((offset, offset + len(line)))
        offset += len(line)
    return spans


def _get_replacement_line_range(
    lines: List[Tuple[int, int]], match_index: int, match_length: int
) -> Tuple[int, int]:
    replacement_end = match_index + match_length
    start_line = -1
    for i, (start, end) in enumerate(lines):
        if start <= match_index < end:
            start_line = i
            break
    if start_line == -1:
        raise ValueError("Replacement range is outside the base content.")
    end_line = start_line
    while end_line < len(lines) and lines[end_line][1] < replacement_end:
        end_line += 1
    if end_line >= len(lines):
        raise ValueError("Replacement range is outside the base content.")
    return start_line, end_line + 1


def _apply_replacements(
    content: str, replacements: List[_MatchedEdit], offset: int = 0
) -> str:
    """倒序应用替换（偏移稳定）。"""
    result = content
    for replacement in sorted(replacements, key=lambda r: r.match_index, reverse=True):
        index = replacement.match_index - offset
        result = (
            result[:index]
            + replacement.new_text
            + result[index + replacement.match_length :]
        )
    return result


def _apply_replacements_preserving_unchanged_lines(
    original_content: str,
    base_content: str,
    replacements: List[_MatchedEdit],
) -> str:
    """fuzzy 空间的替换叠回原文：未触及的行保留原始字节。

    替换范围扩到实际触及的整行，触及行用归一化基底重写，其余行从原文
    复制——重复归一化行不会被对位到错误的出现处。
    """
    original_lines = _split_lines_with_endings(original_content)
    base_lines = _get_line_spans(base_content)
    if len(original_lines) != len(base_lines):
        raise ValueError(
            "Cannot preserve unchanged lines because the base content has a different line count."
        )

    groups: List[dict] = []
    for replacement in sorted(replacements, key=lambda r: r.match_index):
        start_line, end_line = _get_replacement_line_range(
            base_lines, replacement.match_index, replacement.match_length
        )
        current = groups[-1] if groups else None
        if current and start_line < current["endLine"]:
            current["endLine"] = max(current["endLine"], end_line)
            current["replacements"].append(replacement)
            continue
        groups.append(
            {
                "startLine": start_line,
                "endLine": end_line,
                "replacements": [replacement],
            }
        )

    original_line_index = 0
    result = ""
    for group in groups:
        result += "".join(original_lines[original_line_index : group["startLine"]])
        group_start_offset = base_lines[group["startLine"]][0]
        group_end_offset = base_lines[group["endLine"] - 1][1]
        result += _apply_replacements(
            base_content[group_start_offset:group_end_offset],
            group["replacements"],
            group_start_offset,
        )
        original_line_index = group["endLine"]
    result += "".join(original_lines[original_line_index:])
    return result


# ---------------------------------------------------------------------------
# 错误消息
# ---------------------------------------------------------------------------


def _not_found_error(path: str, edit_index: int, total_edits: int) -> ValueError:
    if total_edits == 1:
        return ValueError(
            f"Could not find the exact text in {path}. The old text must match "
            "exactly including all whitespace and newlines."
        )
    return ValueError(
        f"Could not find edits[{edit_index}] in {path}. The oldText must match "
        "exactly including all whitespace and newlines."
    )


def _duplicate_error(
    path: str, edit_index: int, total_edits: int, occurrences: int
) -> ValueError:
    if total_edits == 1:
        return ValueError(
            f"Found {occurrences} occurrences of the text in {path}. The text "
            "must be unique. Please provide more context to make it unique."
        )
    return ValueError(
        f"Found {occurrences} occurrences of edits[{edit_index}] in {path}. "
        "Each oldText must be unique. Please provide more context to make it unique."
    )


def _empty_old_text_error(path: str, edit_index: int, total_edits: int) -> ValueError:
    if total_edits == 1:
        return ValueError(f"oldText must not be empty in {path}.")
    return ValueError(f"edits[{edit_index}].oldText must not be empty in {path}.")


def _no_change_error(path: str, total_edits: int) -> ValueError:
    if total_edits == 1:
        return ValueError(
            f"No changes made to {path}. The replacement produced identical "
            "content. This might indicate an issue with special characters or "
            "the text not existing as expected."
        )
    return ValueError(
        f"No changes made to {path}. The replacements produced identical content."
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def apply_edits_to_normalized_content(
    normalized_content: str, edits: List[Edit], path: str
) -> AppliedEditsResult:
    """对 LF 归一化内容应用一组精确文本替换。

    全部 edit 针对同一份原文匹配，倒序应用保证偏移稳定；任一 edit 需要
    fuzzy 匹配时，整体在归一化空间替换后按行叠回原文。任何校验失败
    （空 oldText / 找不到 / 多处出现 / 区域重叠 / 无变化）直接抛
    ``ValueError``——原子性，调用方不得写盘。
    """
    normalized_edits = [
        Edit(old_text=normalize_to_lf(e.old_text), new_text=normalize_to_lf(e.new_text))
        for e in edits
    ]
    for i, edit in enumerate(normalized_edits):
        if len(edit.old_text) == 0:
            raise _empty_old_text_error(path, i, len(normalized_edits))

    initial_matches = [
        fuzzy_find_text(normalized_content, e.old_text) for e in normalized_edits
    ]
    used_fuzzy_match = any(m.used_fuzzy_match for m in initial_matches)
    replacement_base_content = (
        normalize_for_fuzzy_match(normalized_content)
        if used_fuzzy_match
        else normalized_content
    )

    matched_edits: List[_MatchedEdit] = []
    for i, edit in enumerate(normalized_edits):
        match_result = fuzzy_find_text(replacement_base_content, edit.old_text)
        if not match_result.found:
            raise _not_found_error(path, i, len(normalized_edits))
        occurrences = _count_occurrences(replacement_base_content, edit.old_text)
        if occurrences > 1:
            raise _duplicate_error(path, i, len(normalized_edits), occurrences)
        matched_edits.append(
            _MatchedEdit(
                edit_index=i,
                match_index=match_result.index,
                match_length=match_result.match_length,
                new_text=edit.new_text,
            )
        )

    matched_edits.sort(key=lambda m: m.match_index)
    for prev, current in zip(matched_edits, matched_edits[1:]):
        if prev.match_index + prev.match_length > current.match_index:
            raise ValueError(
                f"edits[{prev.edit_index}] and edits[{current.edit_index}] "
                f"overlap in {path}. Merge them into one edit or target "
                "disjoint regions."
            )

    base_content = normalized_content
    if used_fuzzy_match:
        new_content = _apply_replacements_preserving_unchanged_lines(
            normalized_content, replacement_base_content, matched_edits
        )
    else:
        new_content = _apply_replacements(replacement_base_content, matched_edits)

    if base_content == new_content:
        raise _no_change_error(path, len(normalized_edits))

    return AppliedEditsResult(base_content=base_content, new_content=new_content)


# ---------------------------------------------------------------------------
# Diff 生成
# ---------------------------------------------------------------------------


def generate_unified_patch(
    path: str, old_content: str, new_content: str, context_lines: int = 4
) -> str:
    """生成标准 unified patch。"""
    lines = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
        n=context_lines,
    )
    return "".join(lines)


def generate_diff_string(
    old_content: str, new_content: str, context_lines: int = 4
) -> Tuple[str, Optional[int]]:
    """生成带行号的展示型 diff，返回 (diff, 新文件首个变更行号)。

    变更行带 +/- 前缀与行号，上下文只保留
    变更前后各 context_lines 行，中间以 ... 折叠。
    """
    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")
    max_line_num = max(len(old_lines), len(new_lines))
    width = len(str(max_line_num))

    output: List[str] = []
    first_changed_line: Optional[int] = None
    old_num = 1
    new_num = 1
    # 行级 diff（autojunk=False：重复行多的源码不产生非直觉差异）
    opcodes = list(
        difflib.SequenceMatcher(
            None, old_lines, new_lines, autojunk=False
        ).get_opcodes()
    )

    for idx, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        prev_is_change = idx > 0 and opcodes[idx - 1][0] != "equal"
        next_is_change = idx < len(opcodes) - 1 and opcodes[idx + 1][0] != "equal"

        if tag != "equal":
            if first_changed_line is None:
                first_changed_line = new_num
            for line in old_lines[i1:i2]:
                output.append(f"-{str(old_num).rjust(width)} {line}")
                old_num += 1
            for line in new_lines[j1:j2]:
                output.append(f"+{str(new_num).rjust(width)} {line}")
                new_num += 1
            continue

        # equal 段：仅在与变更相邻时保留上下文，其余整段折叠
        segment = old_lines[i1:i2]
        if prev_is_change and next_is_change:
            if len(segment) <= context_lines * 2:
                for line in segment:
                    output.append(f" {str(old_num).rjust(width)} {line}")
                    old_num += 1
                    new_num += 1
            else:
                for line in segment[:context_lines]:
                    output.append(f" {str(old_num).rjust(width)} {line}")
                    old_num += 1
                    new_num += 1
                skipped = len(segment) - context_lines * 2
                output.append(f" {''.rjust(width)} ...")
                old_num += skipped
                new_num += skipped
                for line in segment[len(segment) - context_lines :]:
                    output.append(f" {str(old_num).rjust(width)} {line}")
                    old_num += 1
                    new_num += 1
        elif prev_is_change:
            for line in segment[:context_lines]:
                output.append(f" {str(old_num).rjust(width)} {line}")
                old_num += 1
                new_num += 1
            skipped = len(segment) - min(context_lines, len(segment))
            if skipped > 0:
                output.append(f" {''.rjust(width)} ...")
                old_num += skipped
                new_num += skipped
        elif next_is_change:
            skipped = max(0, len(segment) - context_lines)
            if skipped > 0:
                output.append(f" {''.rjust(width)} ...")
                old_num += skipped
                new_num += skipped
            for line in segment[skipped:]:
                output.append(f" {str(old_num).rjust(width)} {line}")
                old_num += 1
                new_num += 1
        else:
            old_num += len(segment)
            new_num += len(segment)

    return "\n".join(output), first_changed_line


__all__ = [
    "AppliedEditsResult",
    "Edit",
    "apply_edits_to_normalized_content",
    "detect_line_ending",
    "fuzzy_find_text",
    "generate_diff_string",
    "generate_unified_patch",
    "normalize_for_fuzzy_match",
    "normalize_to_lf",
    "restore_line_endings",
    "strip_bom",
]
