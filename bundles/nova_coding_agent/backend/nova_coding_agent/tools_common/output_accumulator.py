"""流式输出累加器。

对齐 TypeScript ``core/tools/output-accumulator.ts``，供 tool executor 内部使用。
在流式输出过程中以有界内存跟踪尾部内容，并在超出限制时将完整输出落入临时文件。
"""

from __future__ import annotations

import codecs
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

from nova_coding_agent.tools_common.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationOptions,
    TruncationResult,
    truncate_tail,
)


@dataclass
class OutputAccumulatorOptions:
    """输出累加器选项。"""

    max_lines: Optional[int] = None
    max_bytes: Optional[int] = None
    temp_file_prefix: Optional[str] = None


@dataclass
class OutputSnapshot:
    """输出快照。"""

    content: str
    truncation: TruncationResult
    full_output_path: Optional[str] = None


def _default_temp_file_path(prefix: str) -> str:
    fd, path = tempfile.mkstemp(prefix=f"{prefix}-", suffix=".log")
    os.close(fd)
    return path


def _byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


class OutputAccumulator:
    """以有界内存跟踪流式输出的累加器。

    持续追加解码后的文本，仅保留用于展示的尾部文本；当完整输出需要保留时，
    会同时写入临时文件。
    """

    def __init__(self, options: Optional[OutputAccumulatorOptions] = None) -> None:
        opts = options or OutputAccumulatorOptions()
        self._max_lines = (
            opts.max_lines if opts.max_lines is not None else DEFAULT_MAX_LINES
        )
        self._max_bytes = (
            opts.max_bytes if opts.max_bytes is not None else DEFAULT_MAX_BYTES
        )
        self._max_rolling_bytes = max(self._max_bytes * 2, 1)
        self._temp_file_prefix = opts.temp_file_prefix or "nova-output"

        self._raw_chunks: list[bytes] = []
        self._tail_text = ""
        self._tail_bytes = 0
        self._tail_starts_at_line_boundary = True
        self._total_raw_bytes = 0
        self._total_decoded_bytes = 0
        self._completed_lines = 0
        self._total_lines = 0
        self._current_line_bytes = 0
        self._has_open_line = False
        self._finished = False

        self._temp_file_path: Optional[str] = None
        self._temp_file_handle: Optional[object] = None

        # 流式 UTF-8 增量解码器（对齐 TS TextDecoder stream 模式）：
        # 跨 chunk 的多字节字符由解码器内部缓冲，避免逐 chunk 解码产生乱码
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def append(self, data: bytes) -> None:
        """追加一段字节数据。"""
        if self._finished:
            raise RuntimeError("Cannot append to a finished output accumulator")

        self._total_raw_bytes += len(data)
        text = self._decoder.decode(data)
        self._append_decoded_text(text)

        if self._temp_file_handle is not None or self._should_use_temp_file():
            self._ensure_temp_file()
            if self._temp_file_handle is not None:
                self._temp_file_handle.write(data)
                self._temp_file_handle.flush()
        elif data:
            self._raw_chunks.append(data)

    def finish(self) -> None:
        """完成追加，刷新解码器。"""
        if self._finished:
            return
        self._finished = True
        # flush：解出跨 chunk 残余（不完整的尾巴按 replace 处理）
        tail = self._decoder.decode(b"", final=True)
        if tail:
            self._append_decoded_text(tail)
        if self._should_use_temp_file():
            self._ensure_temp_file()

    def snapshot(self, *, persist_if_truncated: bool = False) -> OutputSnapshot:
        """生成当前输出的快照。"""
        tail_truncation = truncate_tail(
            self._get_snapshot_text(),
            TruncationOptions(max_lines=self._max_lines, max_bytes=self._max_bytes),
        )
        truncated = (
            self._total_lines > self._max_lines
            or self._total_decoded_bytes > self._max_bytes
        )
        truncated_by = None
        if truncated:
            truncated_by = tail_truncation.truncated_by or (
                "bytes" if self._total_decoded_bytes > self._max_bytes else "lines"
            )

        truncation = TruncationResult(
            content=tail_truncation.content,
            truncated=truncated,
            truncated_by=truncated_by,
            total_lines=self._total_lines,
            total_bytes=self._total_decoded_bytes,
            output_lines=tail_truncation.output_lines,
            output_bytes=tail_truncation.output_bytes,
            last_line_partial=tail_truncation.last_line_partial,
            first_line_exceeds_limit=tail_truncation.first_line_exceeds_limit,
            max_lines=self._max_lines,
            max_bytes=self._max_bytes,
        )

        if persist_if_truncated and truncation.truncated:
            self._ensure_temp_file()

        return OutputSnapshot(
            content=truncation.content,
            truncation=truncation,
            full_output_path=self._temp_file_path,
        )

    def close_temp_file(self) -> None:
        """关闭临时文件。"""
        if self._temp_file_handle is None:
            return
        handle = self._temp_file_handle
        self._temp_file_handle = None
        handle.close()

    def get_last_line_bytes(self) -> int:
        """返回当前最后一行的字节数。"""
        return self._current_line_bytes

    def _append_decoded_text(self, text: str) -> None:
        if not text:
            return

        bytes_count = _byte_length(text)
        self._total_decoded_bytes += bytes_count
        self._tail_text += text
        self._tail_bytes += bytes_count
        if self._tail_bytes > self._max_rolling_bytes * 2:
            self._trim_tail()

        newlines = text.count("\n")
        last_newline = text.rfind("\n")
        if newlines == 0:
            self._current_line_bytes += bytes_count
            self._has_open_line = True
        else:
            self._completed_lines += newlines
            tail = text[last_newline + 1 :]
            self._current_line_bytes = _byte_length(tail)
            self._has_open_line = len(tail) > 0
        self._total_lines = self._completed_lines + (1 if self._has_open_line else 0)

    def _trim_tail(self) -> None:
        encoded = self._tail_text.encode("utf-8")
        if len(encoded) <= self._max_rolling_bytes:
            self._tail_bytes = len(encoded)
            return

        start = len(encoded) - self._max_rolling_bytes
        while start < len(encoded) and (encoded[start] & 0xC0) == 0x80:
            start += 1

        self._tail_starts_at_line_boundary = (
            start == 0 and self._tail_starts_at_line_boundary
        ) or (start > 0 and encoded[start - 1] == 0x0A)
        trimmed = encoded[start:]
        self._tail_text = trimmed.decode("utf-8", errors="ignore")
        self._tail_bytes = len(trimmed)

    def _get_snapshot_text(self) -> str:
        if self._tail_starts_at_line_boundary:
            return self._tail_text
        first_newline = self._tail_text.find("\n")
        if first_newline == -1:
            return self._tail_text
        return self._tail_text[first_newline + 1 :]

    def _should_use_temp_file(self) -> bool:
        return (
            self._total_raw_bytes > self._max_bytes
            or self._total_decoded_bytes > self._max_bytes
            or self._total_lines > self._max_lines
        )

    def _ensure_temp_file(self) -> None:
        if self._temp_file_path is not None:
            return
        self._temp_file_path = _default_temp_file_path(self._temp_file_prefix)
        self._temp_file_handle = open(self._temp_file_path, "wb")
        for chunk in self._raw_chunks:
            self._temp_file_handle.write(chunk)
        self._raw_chunks = []
        self._temp_file_handle.flush()
