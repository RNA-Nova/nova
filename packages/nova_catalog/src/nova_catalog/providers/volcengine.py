"""Volcengine 的目录构建与 models.dev 原料修正（以真实 API 实测为准）。"""

import copy
from typing import Any, Dict, List, Mapping, Optional, cast

from nova_protocol import KnownApi, MaterializedCatalog, ModelFields, ThinkingLevelMap

from ..define import ProviderContext, define_provider, models_dev_models, stamp_fields

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/"
_DEEPSEEK_V4_COMPAT = {
    "requires_reasoning_content_on_assistant_messages": True,
    "thinking_format": "deepseek",
}
_DEEPSEEK_V4_LEVELS = {
    "off": "none",
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}

# pinned 是完整作者事实：修正直接写入条目，不依赖后续 map 补全。
_PINNED_MODELS: Dict[str, ModelFields] = {
    "deepseek-v4-flash-260425": {
        "id": "deepseek-v4-flash-260425",
        "name": "Deepseek-V4-Flash",
        "reasoning": True,
        "input_types": ["text"],
        "cost": {"input": 1, "output": 2, "cache_read": 0.2, "cache_write": 0.0},
        "context_window": 1048576,
        "max_tokens": 393216,
        "compat": dict(_DEEPSEEK_V4_COMPAT),
        "thinking_level_map": cast("ThinkingLevelMap", dict(_DEEPSEEK_V4_LEVELS)),
    },
    "deepseek-v4-pro-260425": {
        "id": "deepseek-v4-pro-260425",
        "name": "Deepseek-V4-Pro",
        "reasoning": True,
        "input_types": ["text"],
        "cost": {"input": 12, "output": 24, "cache_read": 1, "cache_write": 0.0},
        "context_window": 1048576,
        "max_tokens": 393216,
        "compat": dict(_DEEPSEEK_V4_COMPAT),
        "thinking_level_map": cast("ThinkingLevelMap", dict(_DEEPSEEK_V4_LEVELS)),
    },
}
PINNED_MODEL_IDS = frozenset(_PINNED_MODELS)


def map_volcengine_model(
    fields: ModelFields, _source: Optional[Dict[str, Any]] = None
) -> ModelFields:
    """应用有真实端点证据的火山静态修正；构建/运行时共用。"""
    model_id = fields.get("id") or ""
    if model_id.startswith("deepseek-v4-"):
        fields["compat"] = {**(fields.get("compat") or {}), **_DEEPSEEK_V4_COMPAT}
        fields["thinking_level_map"] = cast(
            "ThinkingLevelMap",
            {
                **(fields.get("thinking_level_map") or {}),
                **_DEEPSEEK_V4_LEVELS,
            },
        )
    if model_id.startswith("doubao-seed-character-"):
        fields["max_tokens"] = 32768
    return fields


def materialize_volcengine_fields(
    remote_fields: List[ModelFields],
    source_status: Mapping[str, str],
) -> MaterializedCatalog:
    """models.dev 原料 → 火山字段 + 显式淘汰集；pinned 永远采用作者事实。"""
    materialized: List[ModelFields] = []
    remove_ids: set[str] = set()
    for fields in remote_fields:
        model_id = fields.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        pinned = _PINNED_MODELS.get(model_id)
        if pinned is not None:
            # 与静态构建的 pinned 优先完全一致：上游后来收录同 ID 时，
            # 也不得用其价格/窗口覆盖经过端点验证的作者事实。
            fields = copy.deepcopy(pinned)
        elif source_status.get(model_id) == "deprecated":
            remove_ids.add(model_id)
            continue
        stamped = stamp_fields(
            model_id,
            fields,
            provider_id="volcengine",
            base_url=BASE_URL,
            api=KnownApi.OPENAI_COMPLETIONS.value,
        )
        materialized.append(
            stamped if pinned is not None else map_volcengine_model(stamped)
        )
    return MaterializedCatalog(
        models_fields=materialized, remove_ids=frozenset(remove_ids)
    )


def _models(ctx: ProviderContext) -> List[ModelFields]:
    return models_dev_models(
        ctx,
        "volcengine",
        api=KnownApi.OPENAI_COMPLETIONS,
        base_url=BASE_URL,
        skip_deprecated=True,
        map_model=map_volcengine_model,
        pinned_models=_PINNED_MODELS,
    )


DEF = define_provider(id="volcengine", models=_models)
