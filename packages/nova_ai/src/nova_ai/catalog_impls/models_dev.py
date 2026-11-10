"""models.dev 公开站（``CatalogService`` 实现，v2 端点）。

单体目录（v2 ``experimental/v2.0/api.json`` 一个 JSON 包装全部厂商）：

- 实例持 60s 去重缓存——一轮刷新的多个 provider 共享一次下载
  （缓存属主 = 实例，组合根负责共享，无模块全局）；
- 无 per-provider 条件请求语义——``validator`` 收下但忽略；
- 翻译唯一点在构建管线（``nova_catalog.sources.models_dev.models_dev_to_nova``，
  v2 形状）——本模块不复刻第二份翻译（v1 双轨已退役）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, cast

from nova_catalog.sources.models_dev import MODELS_DEV_API, models_dev_to_nova
from nova_protocol import AbortedError, AbortSignal, ModelFields, is_aborted

from ..gateway.catalog import CatalogOutcome
from ..utils.http import fetch_json

_MODELS_DEV_CACHE_TTL_S = 60.0


class ModelsDevCatalog:
    """models.dev v2 公开站（单体目录切片 + 实例级 60s 去重缓存）。"""

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
        skip_deprecated: bool = False,
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

        provider = payload.get(key)
        if not isinstance(provider, dict):
            return CatalogOutcome(status=404)
        provider_slice = provider.get("models")
        if not isinstance(provider_slice, dict):
            return CatalogOutcome(status=404)
        # 空 models 是合法空目录（200），可与 provider 不存在（404）区分。

        fields: List[ModelFields] = []
        source_reasoning: Dict[str, Dict[str, Any]] = {}
        for model_id, entry in provider_slice.items():
            mapped = models_dev_to_nova(key, entry, skip_deprecated=skip_deprecated)
            if mapped is not None:
                # 翻译件不携带 id（构建期 id 是 dict key）——运行时 finalize
                # 需要它在字段包里，在此补回。出口一次 cast（与引擎同款纪律：
                # 翻译件的键集即 ModelFields 形状，静态推导不认 dict 直转）
                mapped["id"] = model_id
                fields.append(cast(ModelFields, mapped))
            if isinstance(entry, dict):
                capabilities = entry.get("capabilities") or {}
                reasoning = (
                    capabilities.get("reasoning")
                    if isinstance(capabilities, dict)
                    else None
                )
                if isinstance(reasoning, dict):
                    source_reasoning[model_id] = reasoning
        return CatalogOutcome(
            status=200,
            models_fields=fields,
            source_reasoning=source_reasoning,
            last_modified=int(time.time() * 1000),
        )


__all__ = [
    "MODELS_DEV_API",
    "ModelsDevCatalog",
    "models_dev_to_nova",
]
