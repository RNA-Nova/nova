"""
API 协议实现模块
"""
from typing import Union

# 导出各个提供者的流式函数
from .openai_completions import (
    stream_openai_completions,
    stream_simple_openai_completions,
    OpenAICompletionsOptions,
)

# 提供者流式选项联合类型
ProviderStreamOptions = Union[OpenAICompletionsOptions]

__all__ = [
    "stream_openai_completions",
    "stream_simple_openai_completions",
    "OpenAICompletionsOptions",
    "ProviderStreamOptions",
]
