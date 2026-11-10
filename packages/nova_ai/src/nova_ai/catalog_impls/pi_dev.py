"""pi.dev 公开站（``CatalogService`` 实现）。

per-provider 目录（``GET /api/models/providers/<id>``）+ 真条件请求
（ETag/If-None-Match/304/Last-Modified）——唯一支持条件请求的内置站。
"""

from __future__ import annotations

import re
import time
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, cast

from nova_protocol import AbortedError, AbortSignal, ModelFields, is_aborted

from ..gateway.catalog import CatalogOutcome
from ..utils.http import json_headers, request_with_abort

PI_DEV_CATALOG_BASE_URL = "https://pi.dev"

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _camel_to_snake(key: str) -> str:
    """camelCase → snake_case（compat 键边界归一辅助）。"""
    return _CAMEL_BOUNDARY.sub("_", key).lower()


def _pi_dev_cost(cost: Dict[str, Any]) -> Dict[str, Any]:
    """pi.dev 成本成品 → Nova 字段（含长上下文分层价）。"""
    result: Dict[str, Any] = {
        "input": cost.get("input", 0) or 0,
        "output": cost.get("output", 0) or 0,
        "cache_read": cost.get("cacheRead", 0) or 0,
        "cache_write": cost.get("cacheWrite", 0) or 0,
    }
    tiers = []
    for tier in cost.get("tiers") or []:
        if not isinstance(tier, dict):
            continue
        above = tier.get("inputTokensAbove")
        if not isinstance(above, int) or isinstance(above, bool):
            continue
        tiers.append(
            {
                "input_tokens_above": above,
                "input": tier.get("input", 0) or 0,
                "output": tier.get("output", 0) or 0,
                "cache_read": tier.get("cacheRead", 0) or 0,
                "cache_write": tier.get("cacheWrite", 0) or 0,
            }
        )
    if tiers:
        result["tiers"] = tiers
    return result


def _pi_dev_entry_to_fields(entry: Dict[str, Any]) -> Optional[ModelFields]:
    """pi.dev 单条目（camelCase 成品）→ 字段包（snake_case，待修正+盖戳）。"""
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    context_window = entry.get("contextWindow")
    if not isinstance(context_window, int) or context_window <= 0:
        return None
    max_tokens = entry.get("maxTokens")
    cost = entry.get("cost") or {}
    fields: Dict[str, Any] = {
        "id": model_id,
        "name": entry.get("name") or model_id,
        "reasoning": bool(entry.get("reasoning")),
        "input_types": entry.get("input") or ["text"],
        "cost": _pi_dev_cost(cost),
        "context_window": context_window,
        "max_tokens": max_tokens if isinstance(max_tokens, int) else context_window,
    }
    if isinstance(entry.get("samplingParams"), dict):
        fields["sampling_params"] = entry["samplingParams"]
    if isinstance(entry.get("samplingParamsByThinkingLevel"), dict):
        fields["sampling_params_by_thinking_level"] = entry[
            "samplingParamsByThinkingLevel"
        ]
    # 源数据驱动（三级解析第二级）：携带源的协议标注，盖戳时由 api_for
    # 裁决（本家立场优先，零立场时它说了算）
    if isinstance(entry.get("api"), str) and entry["api"]:
        fields["api"] = entry["api"]
    if entry.get("thinkingLevelMap") is not None:
        fields["thinking_level_map"] = entry["thinkingLevelMap"]
    if entry.get("headers"):
        fields["headers"] = entry["headers"]
    # 输入限制与缓存档寿命（pi Model.inputLimits/promptCache——键集小且
    # 固定，显式逐键翻译；未知键不进）
    input_limits = entry.get("inputLimits")
    if isinstance(input_limits, dict):
        images = input_limits.get("images")
        resize = images.get("resize") if isinstance(images, dict) else None
        translated_limits: Dict[str, Any] = {}
        if isinstance(input_limits.get("maxRequestBytes"), int):
            translated_limits["max_request_bytes"] = input_limits["maxRequestBytes"]
        translated_images: Dict[str, Any] = {}
        if isinstance(resize, dict):
            translated_resize: Dict[str, Any] = {}
            for camel, snake in (
                ("maxWidth", "max_width"),
                ("maxHeight", "max_height"),
                ("maxBytes", "max_bytes"),
                ("jpegQuality", "jpeg_quality"),
            ):
                if isinstance(resize.get(camel), int):
                    translated_resize[snake] = resize[camel]
            if translated_resize:
                translated_images["resize"] = translated_resize
        for camel, snake in (
            ("maxPerMessage", "max_per_message"),
            ("maxPerRequest", "max_per_request"),
        ):
            if isinstance(images, dict) and isinstance(images.get(camel), int):
                translated_images[snake] = images[camel]
        if translated_images:
            translated_limits["images"] = translated_images
        if translated_limits:
            fields["input_limits"] = translated_limits
    prompt_cache = entry.get("promptCache")
    if isinstance(prompt_cache, dict):
        translated_cache = {
            tier: seconds
            for tier, seconds in prompt_cache.items()
            if tier in ("short", "long") and isinstance(seconds, (int, float))
        }
        if translated_cache:
            fields["prompt_cache"] = translated_cache
    # compat 键统一 camelCase → snake_case（边界归一：pi.dev 的 TS 形状
    # 在此翻译成我们的字段形状——compat 类的字段名与 pi 同名仅大小写差异，
    # 机械下划线化即可；未知键同样转换后落 extra，不阻塞）
    if entry.get("compat"):
        fields["compat"] = {
            _camel_to_snake(key): value for key, value in entry["compat"].items()
        }
    # 形状由本函数按 ModelFields 键集构造（dict 直转 TypedDict 静态不认，
    # cast 收口——与 build 的边界校验同款）
    return cast(ModelFields, fields)


class PiDevCatalog:
    """pi.dev 公开站（per-provider 目录 + 真条件请求）。

    返回 404/501 表示该 provider 不在 pi.dev——治理方据此收场。
    """

    def __init__(self, base_url: str = PI_DEV_CATALOG_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")

    async def fetch(
        self,
        key: str,
        signal: Optional[AbortSignal],
        validator: Optional[str],
        skip_deprecated: bool = False,
    ) -> CatalogOutcome:
        """拉取 ``key``（provider id）的 per-provider 目录（带条件请求）。"""
        if is_aborted(signal):
            return CatalogOutcome(status=0)

        headers = json_headers({"if-none-match": validator} if validator else None)

        try:
            response = await request_with_abort(
                "GET",
                f"{self._base_url}/api/models/providers/{key}",
                headers=headers,
                signal=signal,
            )
        except AbortedError:
            raise  # 取消直通（铁律三）
        except Exception as error:
            return CatalogOutcome(status=0, cause=error)

        if response.status_code == 304:
            return CatalogOutcome(status=304)
        if response.status_code != 200:
            # 404/501 = 该 provider 无远程目录；其余非 200 = 瞬时失败——
            # 语义裁决归治理方，站只如实上报状态码
            return CatalogOutcome(status=response.status_code)

        try:
            payload = response.json()
        except Exception as error:
            return CatalogOutcome(status=0, cause=error)
        if not isinstance(payload, dict):
            return CatalogOutcome(status=0, cause=None)

        fields: List[ModelFields] = [
            f
            for f in (_pi_dev_entry_to_fields(entry) for entry in payload.values())
            if f is not None
        ]
        last_modified_header = response.headers.get("last-modified")
        last_modified: Optional[int] = None
        if last_modified_header:
            try:
                last_modified = int(
                    parsedate_to_datetime(last_modified_header).timestamp() * 1000
                )
            except Exception:
                last_modified = None

        return CatalogOutcome(
            status=200,
            models_fields=fields,
            etag=response.headers.get("etag"),
            # 缺失/坏头保持未知（0 等效），不能用“现在”冒充远端新鲜度。
            last_modified=last_modified,
        )


__all__ = [
    "PI_DEV_CATALOG_BASE_URL",
    "PiDevCatalog",
]
