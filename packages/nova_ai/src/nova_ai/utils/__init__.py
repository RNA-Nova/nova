"""
工具函数模块
"""

from .env import (
    get_env_api_key,
    get_env_api_key_typed,
    get_all_env_api_keys,
)

from .copilot import (
    infer_copilot_initiator,
    has_copilot_vision_input,
    build_copilot_dynamic_headers,
    build_copilot_headers_from_messages,
)

from .json_parser import parse_streaming_json

from .surrogate import sanitize_surrogates

from .stream_options import (
    build_base_options,
    clamp_reasoning,
)

from .message_transformer import transform_messages

from .model_utils import (
    calculate_cost,
    supports_xhigh_thinking,
    get_supported_thinking_levels,
)

from .overflow import is_context_overflow

__all__ = [
    # env
    "get_env_api_key",
    "get_env_api_key_typed",
    "get_all_env_api_keys",
    # copilot
    "infer_copilot_initiator",
    "has_copilot_vision_input",
    "build_copilot_dynamic_headers",
    "build_copilot_headers_from_messages",
    # json_parser
    "parse_streaming_json",
    # surrogate
    "sanitize_surrogates",
    # stream_options
    "build_base_options",
    "clamp_reasoning",
    # message_transformer
    "transform_messages",
    # model_utils
    "calculate_cost",
    "supports_xhigh_thinking",
    "get_supported_thinking_levels",
    # overflow
    "is_context_overflow",
]
