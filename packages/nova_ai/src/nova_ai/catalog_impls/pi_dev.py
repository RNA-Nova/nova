"""pi.dev 公开站（``CatalogService`` 实现）。

per-provider 目录（``GET /api/models/providers/<id>``）+ 真条件请求
（ETag/If-None-Match/304/Last-Modified）——唯一支持条件请求的内置站。
"""

from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, cast
from urllib.parse import quote

from nova_async import AbortedError, AbortSignal, is_aborted
from nova_protocol import ModelFields

from ..gateway.catalog import CatalogOutcome
from ..utils.http import get_with_retry, json_headers

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
                "inputTokensAbove": above,
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
    """pi.dev typed 条目 → 字段包；仅真正缺省 type 的旧条目按 chat。"""
    if not isinstance(entry, dict):
        return None
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    model_type = entry["type"] if "type" in entry else "chat"
    if model_type not in ("chat", "image", "classifier"):
        return None

    fields: Dict[str, Any] = {
        "type": model_type,
        "id": model_id,
        "name": entry.get("name") or model_id,
        "input_types": entry.get("input") or ["text"],
        "cost": _pi_dev_cost(entry.get("cost") or {}),
    }
    if model_type == "chat":
        context_window = entry.get("contextWindow")
        if (
            not isinstance(context_window, int)
            or isinstance(context_window, bool)
            or context_window <= 0
        ):
            return None
        max_tokens = entry.get("maxTokens")
        fields.update(
            {
                "reasoning": bool(entry.get("reasoning")),
                "context_window": context_window,
                "max_tokens": (
                    max_tokens
                    if isinstance(max_tokens, int)
                    and not isinstance(max_tokens, bool)
                    and max_tokens > 0
                    else context_window
                ),
            }
        )
    elif model_type == "image":
        output_types = entry.get("output")
        if not isinstance(output_types, list) or "image" not in output_types:
            return None
        fields["output_types"] = output_types
    else:
        context_window = entry.get("contextWindow")
        if (
            not isinstance(context_window, int)
            or isinstance(context_window, bool)
            or context_window <= 0
        ):
            return None
        fields["context_window"] = context_window

    if isinstance(entry.get("samplingParams"), dict) and model_type == "chat":
        fields["sampling_params"] = entry["samplingParams"]
    if (
        isinstance(entry.get("samplingParamsByThinkingLevel"), dict)
        and model_type == "chat"
    ):
        fields["sampling_params_by_thinking_level"] = entry[
            "samplingParamsByThinkingLevel"
        ]
    # 协议与 per-model 端点都随源，不能在后续盖戳时抹掉。
    if isinstance(entry.get("api"), str) and entry["api"]:
        fields["api"] = entry["api"]
    if isinstance(entry.get("baseUrl"), str) and entry["baseUrl"]:
        fields["base_url"] = entry["baseUrl"]
    if entry.get("thinkingLevelMap") is not None and model_type == "chat":
        fields["thinking_level_map"] = entry["thinkingLevelMap"]
    if entry.get("headers"):
        fields["headers"] = entry["headers"]
    # 输入限制对三种操作都有意义；缓存只归聊天，compat 按具体操作验收。
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
    if isinstance(prompt_cache, dict) and model_type == "chat":
        translated_cache = {
            tier: seconds
            for tier, seconds in prompt_cache.items()
            if tier in ("short", "long") and isinstance(seconds, (int, float))
        }
        if translated_cache:
            fields["prompt_cache"] = translated_cache
    if entry.get("compat") and model_type in ("chat", "classifier"):
        fields["compat"] = {
            _camel_to_snake(key): value for key, value in entry["compat"].items()
        }
    return cast(ModelFields, fields)


def _pi_dev_payload_entries(payload: Any) -> Optional[List[Any]]:
    """接受 typed 数组、``{models: [...]}`` 包装与 legacy keyed object。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if "models" in payload:
            models = payload["models"]
            return models if isinstance(models, list) else None
        return list(payload.values())
    return None


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
    ) -> CatalogOutcome:
        """拉取 ``key``（provider id）的 per-provider 目录（带条件请求）。"""
        if is_aborted(signal):
            return CatalogOutcome(status=0)

        headers = json_headers({"if-none-match": validator} if validator else None)

        try:
            response = await get_with_retry(
                f"{self._base_url}/api/models/providers/{quote(key, safe='')}"
                "?types=chat,image,classifier",
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
        entries = _pi_dev_payload_entries(payload)
        if entries is None:
            return CatalogOutcome(status=0, cause=None)

        fields: List[ModelFields] = [
            f
            for f in (_pi_dev_entry_to_fields(entry) for entry in entries)
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
