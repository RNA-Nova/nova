"""JSONL 后端错误类型与文件系统错误映射（对齐 TS ``session/jsonl/errors.ts``）。"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Optional, TypeVar

from ..types import SessionError

__all__ = ["JsonlDecodeError", "file_result", "invalid_file"]

T = TypeVar("T")


class JsonlDecodeError(Exception):
    """JSONL 行解码失败（syntax = 不是合法 JSON；schema = 结构不符合 v4）。"""

    def __init__(self, kind: str, message: str, cause: Optional[BaseException] = None):
        super().__init__(message)
        self.name = "JsonlDecodeError"
        self.kind = kind
        if cause is not None:
            self.__cause__ = cause


async def file_result(operation: Awaitable[T], message: str) -> T:
    """把文件系统错误映射为 :class:`SessionError`（对齐 TS ``fileResult``）。"""
    try:
        return await asyncio.ensure_future(operation)
    except FileNotFoundError as exc:
        raise SessionError("not_found", f"{message}: {exc}", exc) from exc
    except OSError as exc:
        raise SessionError("storage", f"{message}: {exc}", exc) from exc


def invalid_file(path: str, line: int, cause: BaseException) -> SessionError:
    """构造"第 N 行损坏"的会话文件错误。"""
    return SessionError(
        "invalid_entry", f"Invalid JSONL v4 session {path}: line {line} {cause}", cause
    )
