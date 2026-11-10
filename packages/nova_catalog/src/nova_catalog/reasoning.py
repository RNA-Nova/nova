"""models.dev 思考级别推导与 native-effort 门控（pi catalog/reasoning.ts + build.ts）。"""

from __future__ import annotations

from typing import Any, Dict, Optional, cast

from nova_protocol import ModelFields, ThinkingLevelMap

_KNOWN_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")


def effort_to_thinking_level_map(
    reasoning: Optional[Dict[str, Any]],
) -> Optional[ThinkingLevelMap]:
    """官方 effort 值 → 级别表；default/bogus-only 不等于“全级别不支持”。"""
    if not isinstance(reasoning, dict) or reasoning.get("supported") is not True:
        return None
    effort = reasoning.get("effort")
    if not isinstance(effort, list):
        return None
    supported = {v for v in effort if isinstance(v, str)}
    if not supported.intersection({*_KNOWN_LEVELS, "none"}):
        return None
    level_map: ThinkingLevelMap = {"off": "none" if "none" in supported else None}
    for level in _KNOWN_LEVELS:
        level_map[level] = level if level in supported else None  # type: ignore[literal-required]
    return level_map


def sends_native_effort(fields: ModelFields) -> bool:
    """该模型的最终 API/compat 是否把 models.dev effort 原样发给宿主。"""
    api = fields.get("api")
    api_value = getattr(api, "value", api)
    compat = fields.get("compat") or {}
    if api_value == "anthropic-messages":
        return compat.get("force_adaptive_thinking") is True
    if api_value in {
        "openai-responses",
        "azure-openai-responses",
        "openai-codex-responses",
    }:
        return True
    if api_value != "openai-completions":
        return False
    return (
        compat.get("thinking_format", "openai") == "openai"
        and compat.get("supports_reasoning_effort", True) is not False
    )


def apply_models_dev_effort(
    fields: ModelFields, reasoning: Optional[Dict[str, Any]]
) -> None:
    """在 compat/family 已定稿后应用源 effort；已声明的家族值覆盖源值。"""
    if not isinstance(reasoning, dict):
        return
    api = fields.get("api")
    existing = dict(fields.get("thinking_level_map") or {})
    is_anthropic = getattr(api, "value", api) == "anthropic-messages"
    if (
        is_anthropic
        and "off" not in existing
        and (
            reasoning.get("toggle") is True or isinstance(reasoning.get("budget"), dict)
        )
    ):
        # legacy toggle/budget 制不需要 native effort 也能关思考。
        existing["off"] = "disabled"
    if not sends_native_effort(fields):
        if existing:
            fields["thinking_level_map"] = cast("ThinkingLevelMap", existing)
        return
    level_map = effort_to_thinking_level_map(reasoning)
    if level_map is None:
        if existing:
            fields["thinking_level_map"] = cast("ThinkingLevelMap", existing)
        return
    merged = {**level_map, **existing}
    if (
        is_anthropic
        and "off" not in existing
        and merged.get("off") is None
        and (
            reasoning.get("toggle") is True or isinstance(reasoning.get("budget"), dict)
        )
    ):
        merged["off"] = "disabled"
    fields["thinking_level_map"] = cast("ThinkingLevelMap", merged)


__all__ = [
    "apply_models_dev_effort",
    "effort_to_thinking_level_map",
    "sends_native_effort",
]
