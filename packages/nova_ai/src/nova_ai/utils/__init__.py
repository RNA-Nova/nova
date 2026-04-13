"""
工具函数模块
"""

from .env import (
    get_env_api_key,
    get_env_api_key_typed,
    get_all_env_api_keys
)

from .copilot import (
    infer_copilot_initiator,
    has_copilot_vision_input,
    build_copilot_dynamic_headers,
    build_copilot_headers_from_messages
)

from .json_parser import parse_streaming_json

from .surrogate import sanitize_surrogates

from .stream_options import (
    build_base_options,
    clamp_reasoning,
    adjust_max_tokens_for_thinking,
    ThinkingBudgets,
    StreamOptions,
    SimpleStreamOptions,
)

from .message_transformer import transform_messages

from .http_proxy import (
    setup_http_proxy,
    get_http_proxy,
    get_https_proxy,
    configure_http_client_proxy,
    is_node_environment,
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
    "adjust_max_tokens_for_thinking",
    "ThinkingBudgets",
    "StreamOptions",
    "SimpleStreamOptions",
    
    # message_transformer
    "transform_messages",
    
    # http_proxy
    "setup_http_proxy",
    "get_http_proxy",
    "get_https_proxy",
    "configure_http_client_proxy",
    "is_node_environment",

    # over_flow
    "isContextOverflow",
]