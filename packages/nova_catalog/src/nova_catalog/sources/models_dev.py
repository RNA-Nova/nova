"""models.dev v2 源（对位 pi ``catalog/sources/models-dev.ts``）。

构建期单次拉取：整包 payload（实例级 60s 去重——一轮构建内多家
provider 共享一次下载）。v2 的形状红利随翻译全量进来：能力字段
（``capabilities``——tools/reasoning/structured_output）、按上下文
分档的价格（``cost.text.*.overrides`` → cost tiers）、类型与生命周期
（``type``/``status`` 过滤）、**站推导思考级别表**（``reasoning.effort``
→ ``reasoning.py``——手写表退为兜底）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, cast

from nova_protocol import ModelFields

from ..reasoning import effort_to_thinking_level_map
from .http import fetch_json

MODELS_DEV_API = "https://models.dev/experimental/v2.0/api.json"
_CACHE_TTL_S = 60.0


class ModelsDevSource:
    """models.dev v2 单体目录源（构建期用）。"""

    def __init__(self, url: str = MODELS_DEV_API) -> None:
        self._url = url
        self._cached_at = 0.0
        self._cached: Dict[str, Any] | None = None

    async def payload(self) -> Dict[str, Any]:
        """整包目录（60 秒实例内去重）。失败不落缓存，下次真实重试。"""
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < _CACHE_TTL_S:
            return self._cached
        data = await fetch_json(self._url)
        self._cached = data
        self._cached_at = time.monotonic()
        return data

    async def models_for(self, key: str) -> Dict[str, Dict[str, Any]]:
        """取某 provider 键的模型切片（缺键/缺 models 返回空）。"""
        return ((await self.payload()).get(key) or {}).get("models") or {}


# ---------------------------------------------------------------------------
# 成本归一化（v2 cost.text → base + tiers；对位 pi normalizeCost）
# ---------------------------------------------------------------------------


def _first_price(value: Any) -> Optional[float]:
    """单值或 TTL 分档 dict 取首价（``{"5m": 3, "1h": 6}`` → 3；无数值 None）。"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, (int, float)):
                return float(v)
    return None


def _context_threshold(when: Any) -> Optional[float]:
    """``when: {context: {gt|gte: n}}`` → n；其余形态（region/tier/time）不支持。"""
    if not isinstance(when, dict) or set(when.keys()) != {"context"}:
        return None
    ctx = when.get("context") or {}
    size = ctx.get("gt", ctx.get("gte"))
    return float(size) if isinstance(size, (int, float)) else None


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
    text = (v2_cost or {}).get("text") or {}
    v_in, v_out = text.get("input") or {}, text.get("output") or {}
    if (
        not v_in
        or not v_out
        or not _per_token(v_in.get("unit"))
        or not _per_token(v_out.get("unit"))
    ):
        return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    base: Dict[str, Any] = {
        "input": v_in.get("price") or 0,
        "output": v_out.get("price") or 0,
        "cache_read": (v_in.get("cache") or {}).get("read") or 0,
        "cache_write": _cache_write_price(v_in.get("cache")) or 0,
    }
    tiers: Dict[float, Dict[str, Any]] = {}
    for override in v_in.get("overrides") or []:
        above = _context_threshold(override.get("when"))
        if above is None:
            continue
        tier = tiers.setdefault(above, {"inputTokensAbove": above})
        if override.get("price") is not None:
            tier["input"] = override["price"]
        cache = override.get("cache") or {}
        if cache.get("read") is not None:
            tier["cache_read"] = cache["read"]
        cache_write = _cache_write_price(cache)
        if cache_write is not None:
            tier["cache_write"] = cache_write
    for override in v_out.get("overrides") or []:
        above = _context_threshold(override.get("when"))
        if above is None:
            continue
        tier = tiers.setdefault(above, {"inputTokensAbove": above})
        if override.get("price") is not None:
            tier["output"] = override["price"]
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
    if entry.get("type") != "chat":
        return None
    if skip_deprecated and entry.get("status") == "deprecated":
        return None
    capabilities = entry.get("capabilities") or {}
    if (capabilities.get("tools") or {}).get("supported") is not True:
        return None
    model_id = entry.get("id")
    if not model_id:
        if strict:
            raise ValueError(f"{provider_id}: model entry missing id: {entry!r}")
        return None
    name = entry.get("name") or model_id
    limit = entry.get("limit") or {}
    context_window = limit.get("context")
    if not isinstance(context_window, (int, float)) or context_window <= 0:
        context_window = 4096
    max_tokens = limit.get("output")
    if not isinstance(max_tokens, (int, float)) or max_tokens <= 0:
        max_tokens = 4096
    # modalities 可能是裸类型（"text"/"image"）或 MIME（"image/jpeg"…）——
    # image/* 前缀一并识别（对位 pi normalizeModelsDevModel）
    modalities = (entry.get("modalities") or {}).get("input") or ["text"]
    input_types = ["text"]
    if any(m == "image" or str(m).startswith("image/") for m in modalities):
        input_types.append("image")

    reasoning = capabilities.get("reasoning")
    fields: Dict[str, Any] = {
        "name": name,
        "reasoning": isinstance(reasoning, dict) and reasoning.get("supported") is True,
        "input_types": input_types,
        "cost": _normalize_cost(entry.get("cost")),
        "context_window": int(context_window),
        "max_tokens": int(max_tokens),
    }
    # 站推导级别表（v2 红利：effort 值直通——手写表退为填缺兜底）
    level_map = effort_to_thinking_level_map(reasoning)
    if level_map is not None:
        fields["thinking_level_map"] = level_map
    return cast(ModelFields, fields)


__all__ = ["MODELS_DEV_API", "ModelsDevSource", "models_dev_to_nova"]
