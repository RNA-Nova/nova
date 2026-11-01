"""Anthropic 兼容性解析（对齐 TS ``getAnthropicCompat``）。

无 host 检测（TS 侧同）：默认值 + ``model.compat`` 显式覆盖逐字段合并；
``supports_tool_references`` 缺省按模型代际判定（TS
``defaultSupportsToolReferences``：一方 Anthropic、非 Haiku、Claude 4.5+）。
"""

import re
from dataclasses import dataclass
from typing import Optional

from nova_protocol import AnthropicMessagesCompat, Model


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
    supports_tool_references: bool


def _default_supports_tool_references(model: Model) -> bool:
    """tool_reference 的默认支持面（对齐 TS defaultSupportsToolReferences）：

    一方 Anthropic 模型、非 Haiku（Haiku 拒绝客户端 tool_reference 块）、
    Claude 4.5 起（更早的 3.x/4.0/4.1 先于工具搜索特性）。
    """
    if model.provider != "anthropic" or "haiku" in model.id:
        return False
    match = re.match(r"^claude-(?:opus|sonnet|fable)-(\d+)(?:-(\d+))?(?:-|$)", model.id)
    if not match:
        return False
    major = int(match.group(1))
    minor = int(match.group(2)) if match.group(2) and len(match.group(2)) < 8 else 0
    return major > 4 or (major == 4 and minor >= 5)


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
        supports_tool_references=_pick(
            "supports_tool_references", _default_supports_tool_references(model)
        ),
    )


__all__ = ["ResolvedAnthropicCompat", "get_compat"]
