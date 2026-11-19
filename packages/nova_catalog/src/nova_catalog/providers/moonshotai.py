"""Moonshot AI 的目录构建（对位 pi ``catalog/providers/moonshotai.ts``）。"""

from typing import Any, Dict, List

from nova_protocol import KnownApi, ModelCostFields, ModelFields

from ..define import ProviderContext, define_provider, models_dev_models
from ..families.kimi import K3_OPENAI_COMPAT, is_kimi_k3_family_id

BASE_URL = "https://api.moonshot.ai/v1"
MOONSHOTAI_COMPAT = {
    "thinking_format": "deepseek",
    "supports_store": False,
    "supports_developer_role": False,
    "supports_reasoning_effort": False,
    "supports_strict_mode": False,
    "max_tokens_field": "max_tokens",
}
_MID_CONVO_TEXT_MODELS = {"kimi-k2.6", "kimi-k2.7-code", "kimi-k2.7-code-highspeed"}
_ALWAYS_THINKING_MODELS = {"kimi-k2.7-code", "kimi-k2.7-code-highspeed"}


def map_moonshot_model(fields: ModelFields, _source: Dict[str, Any]) -> ModelFields:
    """应用 Moonshot 的静态模型知识与 K3 修正。"""
    model_id = fields.get("id") or ""
    if model_id in _MID_CONVO_TEXT_MODELS:
        fields["compat"] = {
            **(fields.get("compat") or {}),
            "supports_mid_convo_system_messages": True,
        }
    if model_id in _ALWAYS_THINKING_MODELS:
        fields["thinking_level_map"] = {
            **(fields.get("thinking_level_map") or {}),
            "off": None,
        }
    if not is_kimi_k3_family_id(model_id):
        return fields
    fields["reasoning"] = True
    fields["compat"] = {
        **(fields.get("compat") or {}),
        **K3_OPENAI_COMPAT,
        "supports_mid_convo_system_messages": True,
        "supports_mid_convo_tool_additions": True,
    }
    if model_id == "kimi-k3":
        fields["cost"] = {
            "input": 3,
            "output": 15,
            "cache_read": 0.3,
            "cache_write": 0,
        }
        return fields
    cost = ModelCostFields(**(fields.get("cost") or {}))
    cost["cache_write"] = 0
    for tier in cost.get("tiers") or []:
        tier["cache_write"] = 0
    fields["cost"] = cost
    return fields


def moonshot_models(
    ctx: ProviderContext, source: str, base_url: str
) -> List[ModelFields]:
    return models_dev_models(
        ctx,
        source,
        api=KnownApi.OPENAI_COMPLETIONS,
        base_url=base_url,
        map_model=map_moonshot_model,
    )


DEF = define_provider(
    id="moonshotai",
    models=lambda ctx: moonshot_models(ctx, "moonshotai", BASE_URL),
    compat={"openai-completions": MOONSHOTAI_COMPAT},
)
