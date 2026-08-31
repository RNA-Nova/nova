"""通用输出截断工具。

对齐 TypeScript ``core/tools/truncate.ts``，供各 tool executor 内部使用。
提供基于行数和字节数的 head/tail 截断以及单行截长能力。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024  # 50KB
GREP_MAX_LINE_LENGTH = 500  # 单行最大字符数

# find/grep/ls 的输出截断只按字节收口（pi 对这三个工具传 maxLines=Number.MAX_SAFE_INTEGER）：
# 行数已由各工具自身的 limit 参数限制，再叠默认 2000 行上限会提前截断并误报 50KB
UNLIMITED_MAX_LINES = sys.maxsize


@dataclass
class TruncationResult:
    """截断结果。"""

    content: str
    truncated: bool
    truncated_by: Literal["lines", "bytes"] | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool
    first_line_exceeds_limit: bool
    max_lines: int
    max_bytes: int


@dataclass
class TruncationOptions:
    """截断选项。"""

    max_lines: Optional[int] = None
    max_bytes: Optional[int] = None


def _byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def _split_lines_for_counting(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def format_size(bytes_value: int) -> str:
    """将字节数格式化为人类可读字符串。"""
    if bytes_value < 1024:
        return f"{bytes_value}B"
    if bytes_value < 1024 * 1024:
        return f"{(bytes_value / 1024):.1f}KB"
    return f"{(bytes_value / (1024 * 1024)):.1f}MB"


def truncate_head(
    content: str, options: Optional[TruncationOptions] = None
) -> TruncationResult:
    """从头部截断，保留前 N 行/字节。

    不会返回不完整的行。如果第一行就超过字节限制，则返回空内容并标记
    ``first_line_exceeds_limit=True``。
    """
    opts = options or TruncationOptions()
    max_lines = opts.max_lines if opts.max_lines is not None else DEFAULT_MAX_LINES
    max_bytes = opts.max_bytes if opts.max_bytes is not None else DEFAULT_MAX_BYTES

    total_bytes = _byte_length(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=total_lines,
            output_bytes=total_bytes,
            last_line_partial=False,
            first_line_exceeds_limit=False,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    first_line_bytes = _byte_length(lines[0])
    if first_line_bytes > max_bytes:
        return TruncationResult(
            content="",
            truncated=True,
            truncated_by="bytes",
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=0,
            output_bytes=0,
            last_line_partial=False,
            first_line_exceeds_limit=True,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    output_lines_arr: list[str] = []
    output_bytes_count = 0
    truncated_by: Literal["lines", "bytes"] = "lines"

    for i, line in enumerate(lines):
        if i >= max_lines:
            break
        line_bytes = _byte_length(line) + (1 if i > 0 else 0)
        if output_bytes_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        output_lines_arr.append(line)
        output_bytes_count += line_bytes

    if len(output_lines_arr) >= max_lines and output_bytes_count <= max_bytes:
        truncated_by = "lines"

    output_content = "\n".join(output_lines_arr)
    final_output_bytes = _byte_length(output_content)

    return TruncationResult(
        content=output_content,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(output_lines_arr),
        output_bytes=final_output_bytes,
        last_line_partial=False,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def _truncate_string_to_bytes_from_end(text: str, max_bytes: int) -> str:
    """从字符串末尾按字节截断，保留尾部。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    start = len(encoded) - max_bytes
    while start < len(encoded) and (encoded[start] & 0xC0) == 0x80:
        start += 1
    return encoded[start:].decode("utf-8", errors="ignore")


def truncate_tail(
    content: str, options: Optional[TruncationOptions] = None
) -> TruncationResult:
    """从尾部截断，保留最后 N 行/字节。

    适用于 bash 输出等场景。若最后一行本身超过字节限制且尚未加入任何行，
    会返回该行的尾部片段并标记 ``last_line_partial=True``。
    """
    opts = options or TruncationOptions()
    max_lines = opts.max_lines if opts.max_lines is not None else DEFAULT_MAX_LINES
    max_bytes = opts.max_bytes if opts.max_bytes is not None else DEFAULT_MAX_BYTES

    total_bytes = _byte_length(content)
    lines = _split_lines_for_counting(content)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=total_lines,
            output_bytes=total_bytes,
            last_line_partial=False,
            first_line_exceeds_limit=False,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    output_lines_arr: list[str] = []
    output_bytes_count = 0
    truncated_by: Literal["lines", "bytes"] = "lines"
    last_line_partial = False

    for i in range(len(lines) - 1, -1, -1):
        if len(output_lines_arr) >= max_lines:
            break
        line = lines[i]
        line_bytes = _byte_length(line) + (1 if output_lines_arr else 0)
        if output_bytes_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not output_lines_arr:
                truncated_line = _truncate_string_to_bytes_from_end(line, max_bytes)
                output_lines_arr.insert(0, truncated_line)
                output_bytes_count = _byte_length(truncated_line)
                last_line_partial = True
            break
        output_lines_arr.insert(0, line)
        output_bytes_count += line_bytes

    if len(output_lines_arr) >= max_lines and output_bytes_count <= max_bytes:
        truncated_by = "lines"

    output_content = "\n".join(output_lines_arr)
    final_output_bytes = _byte_length(output_content)

    return TruncationResult(
        content=output_content,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(output_lines_arr),
        output_bytes=final_output_bytes,
        last_line_partial=last_line_partial,
        first_line_exceeds_limit=False,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> Tuple[str, bool]:
    """将单行截断到最大字符数，超出部分追加 ``... [truncated]``。"""
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True


def truncate_lines(
    lines: list[str], max_lines: int = DEFAULT_MAX_LINES
) -> Tuple[list[str], bool]:
    """按行数截断，返回 (截断后的行, 是否发生过截断)。"""
    if len(lines) <= max_lines:
        return lines, False
    return lines[:max_lines], True


def trim_trailing_empty_lines(lines: list[str]) -> list[str]:
    """去掉末尾空行。"""
    end = len(lines)
    while end > 0 and lines[end - 1] == "":
        end -= 1
    return lines[:end]
