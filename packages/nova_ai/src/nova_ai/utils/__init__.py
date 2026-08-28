"""
工具函数模块
"""

from .copilot import (
    build_copilot_dynamic_headers,
    build_copilot_headers_from_messages,
    has_copilot_vision_input,
    infer_copilot_initiator,
)
from .env import get_env_api_key
from .estimate import (
    ContextUsageEstimate,
    calculate_context_tokens,
    estimate_context_tokens,
    estimate_message_tokens,
    estimate_text_tokens,
)
from .json_parser import parse_streaming_json
from .message_transformer import transform_messages
from .model_utils import (
    calculate_cost,
    clamp_thinking_level,
    get_supported_thinking_levels,
    has_api,
    models_are_equal,
    to_thinking_level,
)
from .overflow import is_context_overflow
from .simple_options import (
    build_base_options,
    clamp_max_tokens_to_context,
)
from .surrogate import sanitize_surrogates

__all__ = [
    # env
    "get_env_api_key",
    # copilot
    "infer_copilot_initiator",
    "has_copilot_vision_input",
    "build_copilot_dynamic_headers",
    "build_copilot_headers_from_messages",
    # json_parser
    "parse_streaming_json",
    # surrogate
    "sanitize_surrogates",
    # simple_options
    "build_base_options",
    "clamp_max_tokens_to_context",
    # estimate
    "ContextUsageEstimate",
    "calculate_context_tokens",
    "estimate_context_tokens",
    "estimate_message_tokens",
    "estimate_text_tokens",
    # message_transformer
    "transform_messages",
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
