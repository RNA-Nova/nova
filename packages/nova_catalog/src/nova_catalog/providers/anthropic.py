"""Anthropic 直销端的目录知识；跨宿主 Claude 规则归 families/anthropic.py。"""

from typing import Dict, List

from nova_protocol import KnownApi, ModelFields

from ..define import ProviderContext, define_provider
from ..families.anthropic import (
    claude55_thinking_levels,
    enable_claude_mid_convo_effort,
    supports_claude_mid_convo_effort,
    supports_claude_mid_convo_system_messages,
)

_ALLOWED_FALLBACK_MODELS = {
    "claude-fable-5": ["claude-opus-4-8", "claude-opus-5"],
    "claude-opus-5": ["claude-opus-4-8"],
}
_MODELS_WITH_1M_CONTEXT = {"claude-opus-4-6", "claude-sonnet-4-6"}
_MODELS_55 = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}


def _finalize(model_id: str, fields: ModelFields, _ctx: ProviderContext) -> None:
    """应用直销端修正；共享族规则已在 build_catalog 中先执行。"""
    if model_id in _MODELS_WITH_1M_CONTEXT:
        fields["context_window"] = 1000000
    if model_id in _MODELS_55:
        fields["thinking_level_map"] = {
            **(fields.get("thinking_level_map") or {}),
            **claude55_thinking_levels(model_id),
        }
    if supports_claude_mid_convo_effort(model_id):
        enable_claude_mid_convo_effort(fields)
    if supports_claude_mid_convo_system_messages(model_id):
        fields["compat"] = {
            **(fields.get("compat") or {}),
            "supports_mid_convo_system_messages": True,
            "supports_mid_convo_tool_changes": True,
        }
    # 短缓存 300s、长缓存 3600s；只给 Anthropic 直销端标注。
    fields["prompt_cache"] = {"short": 300, "long": 3600}
    context_window = fields.get("context_window") or 0
    fields["input_limits"] = {
        "max_request_bytes": 32 * 1024 * 1024,
        "images": {"max_per_request": 100 if context_window == 200000 else 600},
    }


def _wire_fallback_models(models: List[ModelFields], _ctx: ProviderContext) -> None:
    """连接服务端 fallback，并保留目标模型的本地计费价目。"""
    by_id: Dict[str, ModelFields] = {}
    for model in models:
        model_id = model.get("id")
        if isinstance(model_id, str):
            by_id[model_id] = model
    for model_id, fallback_ids in _ALLOWED_FALLBACK_MODELS.items():
        model = by_id.get(model_id)
        if model is None:
            continue
        source_compat = model.get("compat") or {}
        candidates = (
            [fid for fid in fallback_ids if supports_claude_mid_convo_effort(fid)]
            if source_compat.get("supports_mid_convo_effort")
            else fallback_ids
        )
        entries = [
            {
                "provider": "anthropic",
                "model": fid,
                "cost": dict(by_id[fid].get("cost") or {}),
            }
            for fid in candidates
            if fid in by_id
        ]
        if entries:
            model["compat"] = {
                **source_compat,
                "allowed_fallback_models": entries,
            }


DEF = define_provider(
    id="anthropic",
    name="Anthropic",
    base_url="https://api.anthropic.com",
    api=KnownApi.ANTHROPIC_MESSAGES,
    compat_defaults={"supports_strict_tools": True},
    finalize=_finalize,
    finalize_all=_wire_fallback_models,
)
