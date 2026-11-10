"""Moonshot AI 的构建期知识（对位 pi ``catalog/providers/moonshotai.ts``）。"""

from typing import Any, Dict, cast

from nova_protocol import KnownApi, ModelCostFields, ModelFields

from ..define import define_provider
from ..families.kimi import K3_OPENAI_COMPAT, is_kimi_k3_family_id

_TLM_OFF_ONLY = {"off": None}

MOONSHOTAI_COMPAT_DEFAULTS = {
    "thinking_format": "deepseek",
    "supports_store": False,
    "supports_developer_role": False,
    "supports_reasoning_effort": False,
    "supports_strict_mode": False,
    "max_tokens_field": "max_tokens",
}

MOONSHOTAI_MODEL_CORRECTIONS = {
    # K2.6/K2.7 接受后至的 system 文本，但不接受工具型 system 消息。
    "kimi-k2.6": {"compat": {"supports_mid_convo_system_messages": True}},
    "kimi-k2.7-code": {
        "thinking_level_map": dict(_TLM_OFF_ONLY),
        "compat": {"supports_mid_convo_system_messages": True},
    },
    "kimi-k2.7-code-highspeed": {
        "thinking_level_map": dict(_TLM_OFF_ONLY),
        "compat": {"supports_mid_convo_system_messages": True},
    },
}


def map_moonshot_model(
    model_id: str, fields: ModelFields, _source: Dict[str, Any]
) -> None:
    """在源映射相应用 K3 修正，确保后续共享 effort 能看到其 compat。"""
    if not is_kimi_k3_family_id(model_id):
        return
    fields["reasoning"] = True
    fields["compat"] = {
        **(fields.get("compat") or {}),
        **K3_OPENAI_COMPAT,
        "supports_mid_convo_system_messages": True,
        "supports_mid_convo_tool_additions": True,
    }
    cost = dict(fields.get("cost") or {})
    cost["cache_write"] = 0  # Moonshot 不收缓存写费。
    if model_id == "kimi-k3":
        cost.update({"input": 3, "output": 15, "cache_read": 0.3})
    fields["cost"] = cast("ModelCostFields", cost)


DEF = define_provider(
    id="moonshotai",
    name="Moonshot AI",
    base_url="https://api.moonshot.ai/v1",
    api=KnownApi.OPENAI_COMPLETIONS,
    compat_defaults=MOONSHOTAI_COMPAT_DEFAULTS,
    model_corrections=MOONSHOTAI_MODEL_CORRECTIONS,
    map_model=map_moonshot_model,
)
