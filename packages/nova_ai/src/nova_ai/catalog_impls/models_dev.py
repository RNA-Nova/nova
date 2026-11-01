"""models.dev 公开站（``CatalogService`` 实现）。

单体目录（``https://models.dev/api.json`` 一个 JSON 包装全部厂商）：

- 实例持 60s 去重缓存——一轮刷新的多个 provider 共享一次下载
  （缓存属主 = 实例，组合根负责共享，无模块全局）；
- 无 per-provider 条件请求语义——``validator`` 收下但忽略；
- 方言翻译（``models_dev_to_nova``）与站同居：它是"拉+翻"职责的
  一半，构建期种子流水线归 ``catalog/``（自包含）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from nova_protocol import (
    AbortedError,
    AbortSignal,
    ModelCostFields,
    ModelFields,
    is_aborted,
)

from ..gateway.catalog import CatalogOutcome
from ..utils.http import fetch_json

MODELS_DEV_API = "https://models.dev/api.json"

_MODELS_DEV_CACHE_TTL_S = 60.0


def models_dev_to_nova(
    provider_id: str,
    entry: Dict[str, Any],
    strict: bool = False,
) -> Optional[ModelFields]:
    """models.dev 模型条目 → nova ``Model`` 字段 dict（缺字段返回 None）。

    注意不携带 ``id``——构建期 id 是 dict key（``build_provider_models``
    盖戳时补）；运行时由 ``ModelsDevCatalog.fetch`` 补回字段包。
    """
    model_id = entry.get("id")
    name = entry.get("name")
    if not model_id or not name:
        if strict:
            raise ValueError(f"{provider_id}: model entry missing id/name: {entry!r}")
        return None
    limit = entry.get("limit") or {}
    context_window = limit.get("context")
    max_tokens = limit.get("output")
    if not isinstance(context_window, (int, float)) or context_window <= 0:
        if strict:
            raise ValueError(f"{provider_id}/{model_id}: invalid limit.context")
        return None
    if not isinstance(max_tokens, (int, float)) or max_tokens <= 0:
        if strict:
            raise ValueError(f"{provider_id}/{model_id}: invalid limit.output")
        return None
    cost_source = entry.get("cost") or {}
    cost: ModelCostFields = {
        "input": cost_source.get("input") or 0,
        "output": cost_source.get("output") or 0,
        "cache_read": cost_source.get("cache_read") or 0,
        "cache_write": cost_source.get("cache_write") or 0,
    }
    modalities = (entry.get("modalities") or {}).get("input") or ["text"]
    input_types = [m for m in modalities if m in ("text", "image")] or ["text"]

    fields: ModelFields = {
        "name": name,
        "reasoning": bool(entry.get("reasoning")),
        "input_types": input_types,
        "cost": cost,
        "context_window": int(context_window),
        "max_tokens": int(max_tokens),
    }
    compat: Dict[str, Any] = {}
    if (entry.get("interleaved") or {}).get("field") == "reasoning_content":
        compat["requires_reasoning_content_on_assistant_messages"] = True
    if compat:
        fields["compat"] = compat
    return fields


class ModelsDevCatalog:
    """models.dev 公开站（单体目录切片 + 实例级 60s 去重缓存）。"""

    def __init__(self, url: str = MODELS_DEV_API) -> None:
        self._url = url
        self._cached_at = 0.0
        self._cached: Optional[Dict[str, Any]] = None

    async def payload(self, signal: Optional[AbortSignal] = None) -> Dict[str, Any]:
        """整包目录（60 秒实例内去重——一轮刷新的多个 provider 共享一次下载）。

        无 single-flight：并发首拉各自下一次（去重只覆盖顺序命中）——一轮
        刷新内 provider 串行拉取是常态，不为并发边角加锁。失败不落缓存，
        下次调用真实重试。
        """
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < _MODELS_DEV_CACHE_TTL_S:
            return self._cached
        data = await fetch_json(self._url, signal=signal)
        self._cached = data
        self._cached_at = time.monotonic()
        return data

    async def fetch(
        self,
        key: str,
        signal: Optional[AbortSignal],
        validator: Optional[str],
    ) -> CatalogOutcome:
        """按 ``key``（provider id 或 models.dev 目录键）切片并翻译。"""
        if is_aborted(signal):
            return CatalogOutcome(status=0)

        try:
            payload = await self.payload(signal)
        except AbortedError:
            raise  # 取消直通（铁律三——不许压成"目录不可用"）
        except Exception as error:
            return CatalogOutcome(status=0, cause=error)

        provider_slice = (payload.get(key) or {}).get("models")
        if not provider_slice:
            return CatalogOutcome(status=404)

        fields: List[ModelFields] = []
        for model_id, entry in provider_slice.items():
            mapped = models_dev_to_nova(key, entry)
            if mapped is not None:
                # models_dev_to_nova 不携带 id（构建期 id 是 dict key）——
                # 运行时 finalize 需要它在字段包里，在此补回
                mapped["id"] = model_id
                fields.append(mapped)
        return CatalogOutcome(
            status=200,
            models_fields=fields,
            last_modified=int(time.time() * 1000),
        )


__all__ = [
    "MODELS_DEV_API",
    "ModelsDevCatalog",
    "models_dev_to_nova",
]
