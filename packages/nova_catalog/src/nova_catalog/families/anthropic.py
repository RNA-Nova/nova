"""Claude 模型族的跨宿主事实（pi ``catalog/families/anthropic.ts`` 对位）。"""

from __future__ import annotations

import re
from typing import Any, Dict, cast

from nova_protocol import ModelFields, ThinkingLevelMap

_DISABLED_THINKING_MODELS = {
    "claude-opus-5",
    "claude-haiku-5-5",
    "claude-haiku-5.5",
}
_ADAPTIVE_THINKING = (
    "opus-4-6",
    "opus-4.6",
    "opus-4-7",
    "opus-4.7",
    "opus-4-8",
    "opus-4.8",
    "opus-5",
    "opus.5",
    "sonnet-4-6",
    "sonnet-4.6",
    "sonnet-5",
    "sonnet.5",
    "haiku-5",
    "haiku.5",
    "fable-5",
    "mythos-5",
)
_TEMPERATURE_UNSUPPORTED = (
    "opus-4-7",
    "opus-4.7",
    "opus-4-8",
    "opus-4.8",
    "opus-5",
    "opus.5",
    "sonnet-5-5",
    "sonnet-5.5",
    "haiku-5-5",
    "haiku-5.5",
)
_MAX_EFFORT_ONLY = ("opus-4-6", "opus-4.6", "sonnet-4-6", "sonnet-4.6")
_XHIGH_AND_MAX_EFFORT = (
    "opus-4-7",
    "opus-4.7",
    "opus-4-8",
    "opus-4.8",
    "opus-5",
    "opus.5",
    "sonnet-5",
    "sonnet.5",
    "haiku-5",
    "haiku.5",
)


def _strip_prefix(model_id: str) -> str:
    return re.sub(r"^~?anthropic/", "", model_id.lower())


def supports_claude_mid_convo_effort(model_id: str) -> bool:
    """模型是否接受逐条消息的 effort 变更。"""
    id = _strip_prefix(model_id)
    return bool(
        re.fullmatch(r"claude-opus-(?:5|5[.-]5)(?:-\d{8})?", id)
        or re.fullmatch(r"claude-(?:sonnet|haiku)-5[.-]5(?:-\d{8})?", id)
        or re.fullmatch(r"claude-(?:fable|mythos)-5[.-]1(?:-\d{8})?", id)
    )


def supports_claude_mid_convo_system_messages(model_id: str) -> bool:
    """模型是否接受对话中段的 system 文本与工具变更。"""
    return bool(
        re.fullmatch(r"claude-opus-(?:4[.-]8|5(?:[.-]5)?)(?:-\d{8})?", model_id)
        or re.fullmatch(r"claude-(?:sonnet|haiku)-5[.-]5(?:-\d{8})?", model_id)
        or re.fullmatch(r"claude-(?:fable|mythos)-5(?:[.-]1)?(?:-\d{8})?", model_id)
    )


def _merge_thinking(fields: ModelFields, updates: Dict[str, Any]) -> None:
    fields["thinking_level_map"] = cast(
        "ThinkingLevelMap",
        {**(fields.get("thinking_level_map") or {}), **updates},
    )


def _merge_compat(fields: ModelFields, updates: Dict[str, Any]) -> None:
    fields["compat"] = {**(fields.get("compat") or {}), **updates}


def enable_claude_mid_convo_effort(fields: ModelFields) -> None:
    """开启逐条 effort；不允许 disabled 的模型才移除 off 档位。"""
    _merge_compat(fields, {"supports_mid_convo_effort": True})
    model_id = fields.get("id")
    if not isinstance(model_id, str):
        raise ValueError("Claude model is missing id")
    normalized = re.sub(r"-\d{8}$", "", _strip_prefix(model_id))
    if normalized not in _DISABLED_THINKING_MODELS:
        _merge_thinking(fields, {"off": None})


def claude55_thinking_levels(model_id: str) -> ThinkingLevelMap:
    """5.5 代的级别表兜底；Haiku 5.5 保留关闭能力。"""
    return cast(
        "ThinkingLevelMap",
        {
            **({} if "haiku" in model_id else {"off": None}),
            "minimal": None,
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "xhigh",
            "max": "max",
        },
    )


def apply_claude_family_rules(fields: ModelFields) -> None:
    """应用所有宿主共享的 Claude 级别与 compat 规则。"""
    model_id = fields.get("id")
    if not isinstance(model_id, str):
        raise ValueError("Claude family rule requires model id")
    if any(fragment in model_id for fragment in _MAX_EFFORT_ONLY):
        _merge_thinking(fields, {"max": "max"})
    if any(fragment in model_id for fragment in _XHIGH_AND_MAX_EFFORT):
        _merge_thinking(fields, {"xhigh": "xhigh", "max": "max"})
    if "fable-5" in model_id:
        _merge_thinking(fields, {"off": None, "xhigh": "xhigh", "max": "max"})

    api = fields.get("api")
    if getattr(api, "value", api) != "anthropic-messages":
        return
    if re.fullmatch(r"~anthropic/claude-[a-z]+-latest", model_id) or any(
        fragment in model_id for fragment in _ADAPTIVE_THINKING
    ):
        _merge_compat(fields, {"force_adaptive_thinking": True})
    if any(fragment in model_id.lower() for fragment in _TEMPERATURE_UNSUPPORTED):
        _merge_compat(fields, {"supports_temperature": False})


__all__ = [
    "apply_claude_family_rules",
    "claude55_thinking_levels",
    "enable_claude_mid_convo_effort",
    "supports_claude_mid_convo_effort",
    "supports_claude_mid_convo_system_messages",
]
