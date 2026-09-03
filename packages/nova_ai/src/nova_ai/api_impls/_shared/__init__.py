"""
API 协议实现层共享件（对齐 TS ``src/api/`` 下的跨实现辅助文件）

这里收容被多个协议实现（以及同一实现内部多个文件）共享的实现层逻辑：
消息转换（``transform_messages``）、streamSimple 公共选项（``simple_options``）、
Copilot 动态头部、prompt cache（``prompt_cache``）。

与 ``nova_ai.utils`` 的分界：utils 是跨层通用件（估算、JSON 解析、代理项清理等），
本包是实现层共享件——只服务 API 协议实现，普通调用方不应 import。
"""

from .copilot_headers import (
    build_copilot_dynamic_headers,
    build_copilot_headers_from_messages,
    has_copilot_vision_input,
    infer_copilot_initiator,
)
from .prompt_cache import (
    OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH,
    _add_cache_control_to_last_conversation_message,
    _add_cache_control_to_last_tool,
    _add_cache_control_to_system_prompt,
    _add_cache_control_to_text_content,
    _apply_anthropic_cache_control,
    _get_compat_cache_control,
    clamp_openai_prompt_cache_key,
    resolve_cache_retention,
)
from .simple_options import build_base_options, clamp_max_tokens_to_context
from .transform_messages import transform_messages

__all__ = [
    # transform_messages
    "transform_messages",
    # simple_options
    "build_base_options",
    "clamp_max_tokens_to_context",
    # copilot_headers
    "infer_copilot_initiator",
    "has_copilot_vision_input",
    "build_copilot_dynamic_headers",
    "build_copilot_headers_from_messages",
    # prompt_cache
    "resolve_cache_retention",
    "clamp_openai_prompt_cache_key",
    "OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH",
]
