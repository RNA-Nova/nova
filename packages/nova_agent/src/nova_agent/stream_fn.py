"""全局默认 stream_fn 注册点（对齐 TS ``stream-fn.ts``）。

宿主（如 harness）可在此安装默认模型运行时的 stream 函数，使
``nova_agent`` 不必绑定任何具体 provider 目录。未注册时的兜底是
``nova_ai.builtin_models().stream_simple``（进程内缓存——nova 特有回退，
pi 无此回退，pi 的 getDefaultStreamFn 未配置时直接抛错）。
"""

from __future__ import annotations

import threading
from typing import Optional

from .types.base import StreamFn

_default_stream_fn: Optional[StreamFn] = None
_builtin_fallback: Optional[StreamFn] = None
_lock = threading.Lock()


def set_default_stream_fn(stream_fn: Optional[StreamFn]) -> None:
    """注册/清除全局默认 stream 函数（对齐 TS setDefaultStreamFn）。

    传 ``None`` 清除注册。
    """
    global _default_stream_fn
    with _lock:
        _default_stream_fn = stream_fn


def get_default_stream_fn() -> Optional[StreamFn]:
    """返回已注册的默认 stream 函数；未注册返回 ``None``。"""
    return _default_stream_fn


def builtin_fallback_stream_fn() -> StreamFn:
    """内置目录兜底（每次调用重建——网关是可变运行时容器，进程级缓存会把
    旧 store/auth 状态跨 Agent 共享陈旧化，且使 set_default_stream_fn(None)
    与目录变更失效）。调用频率是每 Agent 构建一次，重建成本可接受。"""
    from nova_ai import builtin_models

    return builtin_models().stream_simple


__all__ = [
    "builtin_fallback_stream_fn",
    "get_default_stream_fn",
    "set_default_stream_fn",
]
