"""models.dev v2 源（对位 pi ``catalog/sources/models-dev.ts``）。

构建期单次拉取：一轮构建读取一次整包 payload。v2 的形状红利随翻译
全量进来：能力字段
（``capabilities``——tools/reasoning/structured_output）、按上下文
分档的价格（``cost.text.*.overrides`` → cost tiers）、类型与生命周期
（``type``/``status`` 过滤）、**站推导思考级别表**（``reasoning.effort``
→ ``reasoning.py``——手写表退为兜底）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, TypeGuard, cast

from nova_protocol import ModelFields

from .http import fetch_json

MODELS_DEV_API = "https://models.dev/experimental/v2.0/api.json"


def _object(value: Any) -> Dict[str, Any]:
    """第三方 JSON 的嵌套对象归一；非对象按无字段处理。"""
    return value if isinstance(value, dict) else {}


def _finite_number(value: Any) -> TypeGuard[float | int]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


class ModelsDevSource:
    """models.dev v2 单体目录源；每次构建调用即一次拉取。"""

    def __init__(self, url: str = MODELS_DEV_API) -> None:
        self._url = url

    async def payload(self) -> Dict[str, Any]:
        """拉取整包目录并校验顶层对象形状。"""
        data = await fetch_json(self._url)
        if not isinstance(data, dict):
            raise ValueError("models.dev payload must be a JSON object")
        return data


# ---------------------------------------------------------------------------
# 成本归一化（v2 cost.text → base + tiers；对位 pi normalizeCost）
# ---------------------------------------------------------------------------


def _first_price(value: Any) -> Optional[float]:
    """单值或 TTL 分档 dict 取首价（``{"5m": 3, "1h": 6}`` → 3；无数值 None）。"""
    if _finite_number(value):
        return float(value)
    if isinstance(value, dict):
        for v in value.values():
            if _finite_number(v):
                return float(v)
    return None


def _context_threshold(when: Any) -> Optional[float]:
    """``when: {context: {gt|gte: n}}`` → n；其余形态（region/tier/time）不支持。"""
    if not isinstance(when, dict) or set(when.keys()) != {"context"}:
        return None
    ctx = _object(when.get("context"))
    size = ctx.get("gt", ctx.get("gte"))
    return float(size) if _finite_number(size) else None


def _cache_write_price(cache: Any) -> Optional[float]:
    """cache write 价（对位 pi ``cacheWritePrice``——``cache.write`` 优先，
    ``cache.explicit.write`` 兜底）。"""
    if not isinstance(cache, dict):
        return None
    price = _first_price(cache.get("write"))
    if price is not None:
        return price
    explicit = cache.get("explicit")
    if isinstance(explicit, dict):
        return _first_price(explicit.get("write"))
    return None


def _per_token(unit: Any) -> bool:
    """计价单位判定（对位 pi ``perToken``——缺席按 token 计）。"""
    return unit is None or unit == "token"


def _normalize_cost(v2_cost: Any) -> Dict[str, Any]:
    """v2 ``cost.text`` → ``{input, output, cache_read, cache_write, tiers?}``。

    只有 context 分档的 overrides 映射为 tiers（其余维度无对应物，pi 同款）；
    非 token 计价的条目返回全零（pi ``normalizeCost`` 的 perToken 闸——
    按次/按张计费不是 token 价，不可冒充）；tier 字段缺省继承基线
    （pi ``modelsDevCost`` 的 ``?? base.X``——稀疏 tier 不吞基线费率）。
    """
    text = _object(_object(v2_cost).get("text"))
    v_in = _object(text.get("input"))
    v_out = _object(text.get("output"))
    if (
        not v_in
        or not v_out
        or not _per_token(v_in.get("unit"))
        or not _per_token(v_out.get("unit"))
    ):
        return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    base: Dict[str, Any] = {
        "input": _first_price(v_in.get("price")) or 0,
        "output": _first_price(v_out.get("price")) or 0,
        "cache_read": _first_price(_object(v_in.get("cache")).get("read")) or 0,
        "cache_write": _cache_write_price(v_in.get("cache")) or 0,
    }
    tiers: Dict[float, Dict[str, Any]] = {}
    overrides = v_in.get("overrides")
    for raw_override in (overrides if isinstance(overrides, list) else []):
        override = _object(raw_override)
        above = _context_threshold(override.get("when"))
        if above is None:
            continue
        tier = tiers.setdefault(above, {"inputTokensAbove": above})
        price = _first_price(override.get("price"))
        if price is not None:
            tier["input"] = price
        cache = _object(override.get("cache"))
        cache_read = _first_price(cache.get("read"))
        if cache_read is not None:
            tier["cache_read"] = cache_read
        cache_write = _cache_write_price(cache)
        if cache_write is not None:
            tier["cache_write"] = cache_write
    overrides = v_out.get("overrides")
    for raw_override in (overrides if isinstance(overrides, list) else []):
        override = _object(raw_override)
        above = _context_threshold(override.get("when"))
        if above is None:
            continue
        tier = tiers.setdefault(above, {"inputTokensAbove": above})
        price = _first_price(override.get("price"))
        if price is not None:
            tier["output"] = price
    if tiers:
        # tier 字段缺省继承基线（pi modelsDevCost 的 `?? base.X`）
        base["tiers"] = [
            {
                "inputTokensAbove": tier["inputTokensAbove"],
                "input": tier.get("input", base["input"]),
                "output": tier.get("output", base["output"]),
                "cache_read": tier.get("cache_read", base["cache_read"]),
                "cache_write": tier.get("cache_write", base["cache_write"]),
            }
            for _, tier in sorted(tiers.items())
        ]
    return base


# ---------------------------------------------------------------------------
# v2 条目 → 字段包（含类型/生命周期/工具过滤——pi 同款）
# ---------------------------------------------------------------------------


def models_dev_to_nova(
    provider_id: str,
    entry: Dict[str, Any],
    strict: bool = False,
    *,
    skip_deprecated: bool = False,
) -> Optional[ModelFields]:
    """models.dev v2 模型条目 → 字段包（不符合/缺字段返回 None）。

    过滤（pi ``modelsDevModels`` 同款）：非 ``chat`` 类型、不支持工具
    调用的一律跳过；``deprecated`` 仅在 ``skip_deprecated`` 时跳过
    （pi 缺省保留——只有个别 provider 开启）。缺名字回落 id、缺窗口/
    上限回落 4096（pi 同款兜底）。形状按 ``ModelFields`` 键集构造
    （dict 直转 TypedDict 静态不认，出口一次 cast——与 pi_dev 同款）。
    """

    def _input_object(value: Any, label: str) -> Dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            if strict:
                raise ValueError(f"{provider_id}: {label} must be an object")
            return {}
        return value

    if not isinstance(entry, dict):
        if strict:
            raise ValueError(f"{provider_id}: model entry must be an object")
        return None
    if entry.get("type") != "chat":
        return None
    if skip_deprecated and entry.get("status") == "deprecated":
        return None
    capabilities = _input_object(entry.get("capabilities"), "capabilities")
    tools = _input_object(capabilities.get("tools"), "capabilities.tools")
    if tools.get("supported") is not True:
        return None
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id:
        if strict:
            raise ValueError(f"{provider_id}: model entry missing id: {entry!r}")
        return None
    raw_name = entry.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else model_id
    limit = _input_object(entry.get("limit"), "limit")
    context_window = limit.get("context")
    if not _finite_number(context_window) or context_window <= 0:
        context_window = 4096
    max_tokens = limit.get("output")
    if not _finite_number(max_tokens) or max_tokens <= 0:
        max_tokens = 4096
    # modalities 可能是裸类型（"text"/"image"）或 MIME（"image/jpeg"…）。
    modalities = _input_object(entry.get("modalities"), "modalities")
    raw_input = modalities.get("input")
    if raw_input is not None and not isinstance(raw_input, list):
        if strict:
            raise ValueError(f"{provider_id}: modalities.input must be an array")
        raw_input = None
    input_modalities = raw_input or ["text"]
    input_types = ["text"]
    if any(m == "image" or str(m).startswith("image/") for m in input_modalities):
        input_types.append("image")

    reasoning = _input_object(capabilities.get("reasoning"), "capabilities.reasoning")
    cost = _input_object(entry.get("cost"), "cost")
    fields: Dict[str, Any] = {
        "name": name,
        "reasoning": reasoning.get("supported") is True,
        "input_types": input_types,
        "cost": _normalize_cost(cost),
        "context_window": int(context_window),
        "max_tokens": int(max_tokens),
    }
    return cast(ModelFields, fields)


__all__ = ["MODELS_DEV_API", "ModelsDevSource", "models_dev_to_nova"]
