"""Anthropic Messages API 实现（对齐 TS ``src/api/anthropic-messages.ts``）。

模块即满足 ``ProviderStreams`` 契约（导出 ``stream`` / ``stream_simple``）。
内部按职责分件：``compat``（compat 解析）/ ``messages``（消息与工具转换）/
``client``（客户端创建）/ ``params``（请求体构建）/ ``_stream``（流式消费）；
跨实现共享件在 ``nova_ai.api_impls._shared``。

基于官方 ``anthropic`` Python SDK（AsyncAnthropic，typed 流式事件）；
SDK 内建重试恒关（max_retries=0），重试与 abort 归本层纪律。
"""

from ._stream import map_stop_reason, stream, stream_simple
from .client import assert_request_auth, create_client, is_oauth_token
from .compat import ResolvedAnthropicCompat, get_compat
from .messages import convert_messages, convert_tools, normalize_tool_call_id
from .options import AnthropicOptions, api
from .params import build_params

__all__ = [
    "AnthropicOptions",
    "ResolvedAnthropicCompat",
    "api",
    "assert_request_auth",
    "build_params",
    "convert_messages",
    "convert_tools",
    "create_client",
    "get_compat",
    "is_oauth_token",
    "map_stop_reason",
    "normalize_tool_call_id",
    "stream",
    "stream_simple",
]
