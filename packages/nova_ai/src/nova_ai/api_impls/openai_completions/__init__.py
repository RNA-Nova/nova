"""
OpenAI Completions API 实现（对齐 TypeScript ``src/api/openai-completions.ts``）

模块即满足 ``ProviderStreams`` 契约（导出 ``stream`` / ``stream_simple``）。
内部按职责分件：``compat``（兼容性检测）/ ``messages``（消息与工具转换）/
``client``（客户端创建）/ ``params``（请求体构建）/ ``reasoning``（reasoning
details 结构化体系）/ ``_stream``（流式消费）；
跨实现共享件在 ``nova_ai.api_impls._shared``。
"""

from .._shared.prompt_cache import (
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
from ._stream import (
    map_stop_reason,
    parse_chunk_usage,
    stream,
    stream_simple,
)
from .client import _has_header, create_client
from .compat import detect_compat, get_compat
from .messages import (
    convert_messages,
    convert_tools,
    get_deferred_tool_names,
    get_tools_by_name,
    has_tool_history,
)
from .options import OpenAICompletionsOptions, api
from .params import build_params
from .reasoning import (
    OPENAI_COMPLETIONS_REASONING_FIELDS,
    append_openai_reasoning_detail,
    is_openai_reasoning_detail,
    is_reasoning_field,
    parse_legacy_encrypted_reasoning_detail,
    parse_openai_reasoning_details,
)

# 提供者流式选项类型（当前唯一实现；新增协议实现时改为 Union）
ProviderStreamOptions = OpenAICompletionsOptions

__all__ = [
    "api",
    "stream",
    "stream_simple",
    "OpenAICompletionsOptions",
    "ProviderStreamOptions",
    "detect_compat",
    "get_compat",
    "build_params",
    "create_client",
    "convert_messages",
    "convert_tools",
    "parse_chunk_usage",
    "map_stop_reason",
    "is_reasoning_field",
    "is_openai_reasoning_detail",
    "parse_openai_reasoning_details",
    "parse_legacy_encrypted_reasoning_detail",
    "append_openai_reasoning_detail",
    "OPENAI_COMPLETIONS_REASONING_FIELDS",
    "resolve_cache_retention",
    "clamp_openai_prompt_cache_key",
    "OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH",
]
