"""
API 协议实现模块

每个子模块对应一种 API 协议，导出 ``stream`` / ``stream_simple`` 两个函数，
直接满足 ``ProviderStreams`` 契约（对齐 TS ``src/api/*.ts``）。
"""

from .openai_completions import (
    OpenAICompletionsOptions,
    stream,
    stream_simple,
)

# 提供者流式选项类型（当前唯一实现；新增协议实现时改为 Union）
ProviderStreamOptions = OpenAICompletionsOptions

__all__ = [
    "stream",
    "stream_simple",
    "OpenAICompletionsOptions",
    "ProviderStreamOptions",
]
