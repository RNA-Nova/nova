"""Kimi Coding 的构建期知识（对位 pi ``catalog/providers/kimi-coding.ts``）。"""

from typing import Any, Dict, cast

from nova_protocol import KnownApi, ModelCostFields, ModelFields

from ..define import ProviderContext, define_provider
from ..families.kimi import K3_OPENAI_COMPAT

# 订阅折算价：只填源站的零价/缺价（pi ``source price || implied cost``）。
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


def _map_model(_model_id: str, fields: ModelFields, source: Dict[str, Any]) -> None:
    """源声明 effort 时启用 K3-style；该相位先于共享 effort 推导。"""
    capabilities = source.get("capabilities")
    if not isinstance(capabilities, dict):
        return
    reasoning = capabilities.get("reasoning")
    if not isinstance(reasoning, dict) or not isinstance(reasoning.get("effort"), list):
        return
    fields["compat"] = {
        **(fields.get("compat") or {}),
        **K3_OPENAI_COMPAT,
        "supports_mid_convo_tool_additions": True,
    }


def _finalize(model_id: str, fields: ModelFields, _ctx: ProviderContext) -> None:
    implied = _IMPLIED_COSTS.get(model_id)
    if implied is not None:
        cost = dict(fields.get("cost") or {})
        for key, value in implied.items():
            if not cost.get(key):
                cost[key] = value
        fields["cost"] = cast("ModelCostFields", cost)


DEF = define_provider(
    id="kimi-coding",
    name="Kimi Coding",
    base_url="https://api.kimi.com/coding/v1",
    api=KnownApi.OPENAI_COMPLETIONS,
    models_dev_key="kimi-code-plan-global",
    compat_defaults={
        "thinking_format": "deepseek",
        "supports_store": False,
        "supports_developer_role": False,
        "supports_reasoning_effort": False,
        "supports_strict_mode": False,
        "max_tokens_field": "max_tokens",
        "supports_mid_convo_system_messages": True,
    },
    model_corrections={
        # 全部模型 always-thinking；其余源推导级别保留。
        "*": {"thinking_level_map": {"off": None}},
    },
    map_model=_map_model,
    finalize=_finalize,
)
