"""OpenRouter 图像与分类目录源（对位 pi ``catalog/sources/openrouter.ts``）。

分别消费 ``output_modalities=image`` 与 ``decisions`` 列表；聊天不在本模块。
价格从 OpenRouter 的美元/token 字符串转换为美元/百万 token。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, cast

from nova_protocol import ModelCostFields, ModelFields

from .http import fetch_json

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_IMAGES_URL = f"{OPENROUTER_MODELS_URL}?output_modalities=image"
OPENROUTER_DECISIONS_URL = f"{OPENROUTER_MODELS_URL}?output_modalities=decisions"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_IMAGES_API = "openrouter-images"
OPENROUTER_CLASSIFIERS_API = "typesafe-system-one"


class OpenRouterImageSource:
    """OpenRouter 图像模型列表；每次构建只拉取一次。"""

    def __init__(self, url: str = OPENROUTER_IMAGES_URL) -> None:
        self._url = url

    async def payload(self) -> List[Dict[str, Any]]:
        data = await fetch_json(self._url)
        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ValueError("OpenRouter image catalog must contain a data array")
        return entries


class OpenRouterDecisionSource:
    """OpenRouter 分类模型列表；每次构建只拉取一次。"""

    def __init__(self, url: str = OPENROUTER_DECISIONS_URL) -> None:
        self._url = url

    async def payload(self) -> List[Dict[str, Any]]:
        data = await fetch_json(self._url)
        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ValueError("OpenRouter decision catalog must contain a data array")
        return entries


def _per_million(
    value: Any, fallback: float, *, strict: bool = False, label: str = "price"
) -> float:
    """OpenRouter 字符串单价 → 每百万 token；strict 下畸形值响亮失败。"""
    valid = not isinstance(value, bool)
    try:
        parsed = float(value) if valid else 0.0
    except (TypeError, ValueError):
        valid = False
        parsed = 0.0
    valid = valid and math.isfinite(parsed)
    if not valid:
        if strict:
            raise ValueError(f"OpenRouter {label} must be a finite number")
        return fallback
    # OpenRouter 用 -1 表示路由器模型要等业务模型选定后才有价格；按 pi 回退。
    if parsed < 0:
        return fallback
    return round(parsed * 1_000_000, 6)


_PRICE_KEYS = {
    "prompt": "input",
    "completion": "output",
    "input_cache_read": "cache_read",
    "input_cache_write": "cache_write",
}
_OVERRIDE_KEYS = frozenset({*_PRICE_KEYS, "min_prompt_tokens"})


def openrouter_cost(
    pricing: Any, *, strict: bool = False, label: str = "model"
) -> Dict[str, Any]:
    """翻译基础价与 prompt-length tiers；未知条件跳过，同阈值后者逐键胜。"""
    if not isinstance(pricing, dict):
        if strict:
            raise ValueError(f"OpenRouter {label} pricing must be an object")
        pricing = {}
    base = {
        target: (
            _per_million(pricing[source], 0, strict=strict, label=f"{label}.{source}")
            if source in pricing
            else 0
        )
        for source, target in _PRICE_KEYS.items()
    }
    tiers_by_threshold: Dict[int, Dict[str, Any]] = {}
    overrides = pricing.get("overrides")
    if strict and "overrides" in pricing and not isinstance(overrides, list):
        raise ValueError(f"OpenRouter {label}.overrides must be an array")
    for index, entry in enumerate(overrides if isinstance(overrides, list) else []):
        item_label = f"{label}.overrides[{index}]"
        if not isinstance(entry, dict):
            if strict:
                raise ValueError(f"OpenRouter {item_label} must be an object")
            continue
        # 含任何未识别条件的条目不能应用，避免未来条件价被误当全局价。
        if set(entry) - _OVERRIDE_KEYS:
            continue
        above = entry.get("min_prompt_tokens")
        if not isinstance(above, int) or isinstance(above, bool) or above < 0:
            if strict and above is not None:
                raise ValueError(
                    f"OpenRouter {item_label}.min_prompt_tokens must be a non-negative integer"
                )
            continue
        tier = tiers_by_threshold.setdefault(above, {"inputTokensAbove": above, **base})
        for source, target in _PRICE_KEYS.items():
            if source in entry:
                tier[target] = _per_million(
                    entry[source],
                    tier[target],
                    strict=strict,
                    label=f"{item_label}.{source}",
                )
    tiers = list(tiers_by_threshold.values())
    return {**base, **({"tiers": tiers} if tiers else {})}


def _modalities(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(value for value in values if value in ("text", "image")))


def openrouter_images_to_nova(
    provider_id: str, entries: List[Dict[str, Any]], *, strict: bool = False
) -> List[ModelFields]:
    """OpenRouter 图像列表 → 图像模型字段包；同 id 首个条目获胜。"""
    models: List[ModelFields] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            if strict:
                raise ValueError(f"{provider_id}: image entry must be an object")
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id:
            if strict:
                raise ValueError(f"{provider_id}: image model requires a non-empty id")
            continue
        if model_id in seen:
            continue
        architecture = entry.get("architecture")
        if not isinstance(architecture, dict):
            if strict and "architecture" in entry:
                raise ValueError(
                    f"{provider_id}/{model_id}: architecture must be an object"
                )
            architecture = {}
        for key in ("input_modalities", "output_modalities"):
            values = architecture.get(key)
            if (
                strict
                and key in architecture
                and (
                    not isinstance(values, list)
                    or any(not isinstance(value, str) for value in values)
                )
            ):
                raise ValueError(
                    f"{provider_id}/{model_id}: architecture.{key} must be an array of strings"
                )
        output = _modalities(architecture.get("output_modalities"))
        if "image" not in output:
            continue
        input_types = _modalities(architecture.get("input_modalities")) or ["text"]
        name = entry.get("name")
        if strict and "name" in entry and (not isinstance(name, str) or not name):
            raise ValueError(
                f"{provider_id}/{model_id}: name must be a non-empty string"
            )
        seen.add(model_id)
        pricing = entry["pricing"] if "pricing" in entry else {}
        models.append(
            {
                "type": "image",
                "id": model_id,
                "name": name if isinstance(name, str) and name else model_id,
                "api": OPENROUTER_IMAGES_API,
                "provider": provider_id,
                "base_url": OPENROUTER_BASE_URL,
                "input_types": input_types,
                "output_types": output,
                "cost": cast(
                    "ModelCostFields",
                    openrouter_cost(
                        pricing, strict=strict, label=f"{provider_id}/{model_id}"
                    ),
                ),
            }
        )
    return models


def openrouter_decisions_to_nova(
    provider_id: str, entries: List[Dict[str, Any]], *, strict: bool = False
) -> List[ModelFields]:
    """OpenRouter decisions 列表 → System One 分类模型；同 id 首个获胜。"""
    models: List[ModelFields] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            if strict:
                raise ValueError(f"{provider_id}: decision entry must be an object")
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id:
            if strict:
                raise ValueError(
                    f"{provider_id}: decision model requires a non-empty id"
                )
            continue
        if model_id in seen:
            continue
        architecture = entry.get("architecture")
        if not isinstance(architecture, dict):
            if strict and "architecture" in entry:
                raise ValueError(
                    f"{provider_id}/{model_id}: architecture must be an object"
                )
            architecture = {}
        for key in ("input_modalities", "output_modalities"):
            values = architecture.get(key)
            if (
                strict
                and key in architecture
                and (
                    not isinstance(values, list)
                    or any(not isinstance(value, str) for value in values)
                )
            ):
                raise ValueError(
                    f"{provider_id}/{model_id}: architecture.{key} must be an array of strings"
                )
        output = architecture.get("output_modalities")
        if not isinstance(output, list) or "decisions" not in output:
            continue
        input_types = _modalities(architecture.get("input_modalities")) or ["text"]
        name = entry.get("name")
        if strict and "name" in entry and (not isinstance(name, str) or not name):
            raise ValueError(
                f"{provider_id}/{model_id}: name must be a non-empty string"
            )
        top_provider = entry.get("top_provider")
        top_context = (
            top_provider.get("context_length")
            if isinstance(top_provider, dict)
            else None
        )
        # pi 的真值级联：top 的 0/None 都要继续回退到条目自身窗口。
        raw_context = top_context or entry.get("context_length")
        valid_context = (
            isinstance(raw_context, int)
            and not isinstance(raw_context, bool)
            and raw_context > 0
        )
        # OpenRouter 以 0 表示未知窗口（pi 的 `|| 4096` 同款）；这不是坏数据。
        if (
            strict
            and raw_context is not None
            and not valid_context
            and (isinstance(raw_context, bool) or raw_context != 0)
        ):
            raise ValueError(
                f"{provider_id}/{model_id}: context length must be a positive integer"
            )
        seen.add(model_id)
        models.append(
            {
                "type": "classifier",
                "id": model_id,
                "name": name if isinstance(name, str) and name else model_id,
                "api": OPENROUTER_CLASSIFIERS_API,
                "provider": provider_id,
                "base_url": OPENROUTER_BASE_URL,
                "input_types": input_types,
                "cost": cast(
                    "ModelCostFields",
                    openrouter_cost(
                        entry.get("pricing", {}),
                        strict=strict,
                        label=f"{provider_id}/{model_id}",
                    ),
                ),
                "context_window": (
                    raw_context
                    if isinstance(raw_context, int)
                    and not isinstance(raw_context, bool)
                    and raw_context > 0
                    else 4096
                ),
                # OpenRouter 丢弃 assets；图片以 image_url 块放进 state。
                "compat": {
                    "image_input": "state-image-urls",
                    "send_image_opt_in_header": False,
                },
            }
        )
    return models


__all__ = [
    "OPENROUTER_BASE_URL",
    "OPENROUTER_CLASSIFIERS_API",
    "OPENROUTER_DECISIONS_URL",
    "OPENROUTER_IMAGES_API",
    "OPENROUTER_IMAGES_URL",
    "OpenRouterDecisionSource",
    "OpenRouterImageSource",
    "openrouter_cost",
    "openrouter_decisions_to_nova",
    "openrouter_images_to_nova",
]
