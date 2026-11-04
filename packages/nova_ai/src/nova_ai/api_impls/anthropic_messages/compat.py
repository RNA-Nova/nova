"""Anthropic 兼容性解析（对齐 TS ``getAnthropicCompat``）。

无 host 检测（TS 侧同）：默认值 + ``model.compat`` 显式覆盖逐字段合并。
"""

from dataclasses import dataclass
from typing import List, Optional

from nova_protocol import AnthropicAllowedFallbackModel, AnthropicMessagesCompat, Model


@dataclass(frozen=True, kw_only=True)
class ResolvedAnthropicCompat:
    """默认值合并后的 compat 视图（组件内值对象——规则 5）。"""

    supports_eager_tool_input_streaming: bool
    supports_long_cache_retention: bool
    send_session_affinity_headers: bool
    supports_cache_control_on_tools: bool
    supports_temperature: bool
    supports_strict_tools: bool
    allow_empty_signature: bool
    force_adaptive_thinking: bool
    supports_mid_convo_system_messages: bool
    supports_mid_convo_tool_changes: bool
    supports_mid_convo_effort: bool
    supports_message_threads: bool
    # 服务端 refusal fallback 许可名单（None/空 = 不发 fallbacks 字段）
    allowed_fallback_models: Optional[List[AnthropicAllowedFallbackModel]]


def get_compat(model: Model) -> ResolvedAnthropicCompat:
    """解析模型的 compat（默认值垫底，``model.compat`` 显式字段覆盖）。"""
    compat = model.compat
    overrides = compat if isinstance(compat, AnthropicMessagesCompat) else None

    def _pick(name: str, default: bool) -> bool:
        value = getattr(overrides, name, None) if overrides is not None else None
        return default if value is None else value

    return ResolvedAnthropicCompat(
        supports_eager_tool_input_streaming=_pick(
            "supports_eager_tool_input_streaming", True
        ),
        supports_long_cache_retention=_pick("supports_long_cache_retention", True),
        send_session_affinity_headers=_pick("send_session_affinity_headers", False),
        supports_cache_control_on_tools=_pick("supports_cache_control_on_tools", True),
        supports_temperature=_pick("supports_temperature", True),
        supports_strict_tools=_pick("supports_strict_tools", False),
        allow_empty_signature=_pick("allow_empty_signature", False),
        force_adaptive_thinking=_pick("force_adaptive_thinking", False),
        supports_mid_convo_system_messages=_pick(
            "supports_mid_convo_system_messages", False
        ),
        supports_mid_convo_tool_changes=_pick("supports_mid_convo_tool_changes", False),
        supports_mid_convo_effort=_pick("supports_mid_convo_effort", False),
        # 默认限 Anthropic 自有端点（对齐 TS）：丢未知请求字段的网关会把
        # continue 静默变成丢失前文的请求；转发 thread 字段的网关可显式开
        supports_message_threads=_pick(
            "supports_message_threads",
            model.provider == "anthropic" and "api.anthropic.com" in model.base_url,
        ),
        # 名单直通（无检测默认——对齐 TS：缺省/空即省略 fallbacks 字段）
        allowed_fallback_models=(
            overrides.allowed_fallback_models if overrides is not None else None
        ),
    )


__all__ = ["ResolvedAnthropicCompat", "get_compat"]
