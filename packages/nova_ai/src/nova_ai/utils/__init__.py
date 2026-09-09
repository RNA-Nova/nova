"""
跨层通用工具函数

实现层共享件（消息转换 / streamSimple 选项 / Copilot 头部 / prompt cache）
已迁至 ``nova_ai.api_impls._shared``——本模块只收容跨层通用件。
"""

from .env import get_env_api_key
from .estimate import (
    ContextUsageEstimate,
    calculate_context_tokens,
    estimate_context_tokens,
    estimate_message_tokens,
    estimate_text_tokens,
)
from .json_parser import StreamingJsonParser, parse_streaming_json
from .model_utils import (
    calculate_cost,
    clamp_thinking_level,
    get_supported_thinking_levels,
    has_api,
    models_are_equal,
    to_thinking_level,
)
from .overflow import is_context_overflow
from .surrogate import sanitize_surrogates

__all__ = [
    # env
    "get_env_api_key",
    # json_parser
    "StreamingJsonParser",
    "parse_streaming_json",
    # surrogate
    "sanitize_surrogates",
    # estimate
    "ContextUsageEstimate",
    "calculate_context_tokens",
    "estimate_context_tokens",
    "estimate_message_tokens",
    "estimate_text_tokens",
    # model_utils
    "calculate_cost",
    "clamp_thinking_level",
    "get_supported_thinking_levels",
    "to_thinking_level",
    "has_api",
    "models_are_equal",
    # overflow
    "is_context_overflow",
]
