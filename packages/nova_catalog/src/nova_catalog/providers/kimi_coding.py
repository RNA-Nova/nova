"""Kimi Coding 的目录构建（对位 pi ``catalog/providers/kimi-coding.ts``）。"""

from typing import Any, Dict, List, cast

from nova_protocol import KnownApi, ModelCostFields, ModelFields

from ..define import ProviderContext, define_provider, models_dev_models
from ..families.kimi import K3_OPENAI_COMPAT
from ..reasoning import effort_to_thinking_level_map

BASE_URL = "https://api.kimi.com/coding/v1"
_IMPLIED_COSTS = {
    "kimi-for-coding": {
        "input": 0.95,
        "output": 4,
        "cache_read": 0.19,
        "cache_write": 0,
    },
    "k3": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 0},
    "k3-256k": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 0},
    "k3.1-pro": {"input": 2, "output": 10, "cache_read": 0.2, "cache_write": 0},
    "k3.1-pro-highspeed": {
        "input": 4,
        "output": 20,
        "cache_read": 0.2,
        "cache_write": 0,
    },
    "kimi-for-coding-highspeed": {
        "input": 1.9,
        "output": 8,
        "cache_read": 0.38,
        "cache_write": 0,
    },
}


def _map_model(fields: ModelFields, source: Dict[str, Any]) -> ModelFields:
    """K3-style 由源声明的 effort 判定；compat 与级别在本地一次落齐。"""
    fields["thinking_level_map"] = {
        **(fields.get("thinking_level_map") or {}),
        "off": None,
    }
    capabilities = source.get("capabilities")
    reasoning = (
        capabilities.get("reasoning") if isinstance(capabilities, dict) else None
    )
    if (
        not isinstance(reasoning, dict)
        or reasoning.get("supported") is not True
        or not isinstance(reasoning.get("effort"), list)
    ):
        return fields
    fields["compat"] = {
        **(fields.get("compat") or {}),
        **K3_OPENAI_COMPAT,
        "supports_mid_convo_tool_additions": True,
    }
    effort = effort_to_thinking_level_map(reasoning) or {}
    fields["thinking_level_map"] = {
        **effort,
        **(fields.get("thinking_level_map") or {}),
    }
    return fields


def _models(ctx: ProviderContext) -> List[ModelFields]:
    return models_dev_models(
        ctx,
        "kimi-code-plan-global",
        api=KnownApi.OPENAI_COMPLETIONS,
        base_url=BASE_URL,
        map_model=_map_model,
        record_reasoning=False,
    )


def _finalize(fields: ModelFields, _ctx: ProviderContext) -> None:
    implied = _IMPLIED_COSTS.get(fields.get("id") or "")
    if implied is not None:
        cost = dict(fields.get("cost") or {})
        for key, value in implied.items():
            if not cost.get(key):
                cost[key] = value
        # 订阅折算价是平坦费率，源零价的超档价格不能继续生效。
        cost.pop("tiers", None)
        fields["cost"] = cast("ModelCostFields", cost)


DEF = define_provider(
    id="kimi-coding",
    models=_models,
    compat={
        "openai-completions": {
            "thinking_format": "deepseek",
            "supports_store": False,
            "supports_developer_role": False,
            "supports_reasoning_effort": False,
            "supports_strict_mode": False,
            "max_tokens_field": "max_tokens",
            "supports_mid_convo_system_messages": True,
        }
    },
    finalize=_finalize,
)
