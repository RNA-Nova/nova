"""全局默认 stream_fn 注册点（对齐 pi ``stream-fn.ts``）。

宿主（如 harness）在启动时经 ``set_default_stream_fn`` 安装默认模型运行时的
stream 函数，之后 ``Agent`` 与低层循环省略 ``stream_fn`` 时都走它。

未注册且未显式传参时 ``get_default_stream_fn`` 抛错（fail-fast，pi 同款）：
本包不做内置目录兜底——静默回退会让"忘了配 stream_fn"变成"悄悄用环境变量
凭据发真实请求"。库消费者应显式传 ``stream_fn``（如
``builtin_models().stream_simple``）。
"""

from __future__ import annotations

import threading
from typing import Optional

from .types.base import StreamFn

_default_stream_fn: Optional[StreamFn] = None
_lock = threading.Lock()


def set_default_stream_fn(stream_fn: Optional[StreamFn]) -> None:
    """注册/清除全局默认 stream 函数（对齐 pi setDefaultStreamFn）。

    传 ``None`` 清除注册。
    """
    global _default_stream_fn
    with _lock:
        _default_stream_fn = stream_fn


def get_default_stream_fn() -> StreamFn:
    """返回已注册的默认 stream 函数；未注册时抛错（对齐 pi getDefaultStreamFn）。"""
    if _default_stream_fn is None:
        raise RuntimeError(
            "No default stream function configured. "
            "Pass stream_fn explicitly or call set_default_stream_fn()."
        )
    return _default_stream_fn


__all__ = [
    "get_default_stream_fn",
    "set_default_stream_fn",
]
