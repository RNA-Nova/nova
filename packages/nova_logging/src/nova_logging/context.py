"""受控日志上下文：contextvars 绑定、嵌套恢复与字段校验。"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Optional

from structlog.contextvars import bound_contextvars

from .events import CONTEXT_FIELDS


@contextmanager
def log_context(**fields: Optional[str]) -> Generator[None, None, None]:
    """绑定一次逻辑操作的日志上下文，退出时恢复外层值。

    上下文只放身份与归属，不放状态、正文或完整模型对象；未知字段和
    非字符串值在设计期直接报错，避免把任意 payload 带进所有日志。
    """
    unknown = fields.keys() - CONTEXT_FIELDS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown logging context fields: {names}")
    for name, value in fields.items():
        if value is not None and not isinstance(value, str):
            raise TypeError(f"Logging context field {name} must be a string or None")
    with bound_contextvars(**fields):
        yield


__all__ = ["log_context"]
