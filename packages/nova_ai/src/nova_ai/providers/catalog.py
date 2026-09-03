"""模型目录数据源与映射（构建期种子与运行时物化共用的单源）。

- **数据源**：models.dev/api.json（主源）+ 火山方舟 ``/api/v3/models``
  （生命周期状态，需凭据）；kimi-coding 为订阅制，以本模块手写权威块为准。
- **修正层**：``thinking_level_map`` / compat 语义 / 钉住模型——厂商 API 与
  models.dev 都不提供的知识，人只维护这里。
- **消费者**：``scripts/generate_models.py``（构建期种子发射）与
  ``providers/remote_catalog.py``（运行时物化包装）。

一次模型目录的刷新 = 拉源 → 映射 → 修正 → 输出最终 ``Model`` 字段，
两个入口共用同一份映射代码，保证种子与物化永不漂移。
"""

from __future__ import annotations

import fnmatch
import time
from typing import Any, Dict, List, Optional, Tuple, TypedDict, cast

MODELS_DEV_API = "https://models.dev/api.json"
ARK_MODELS_URL = "https://ark.cn-beijing.volces.com/api/v3/models"
ARK_BAD_STATUS = {"Shutdown", "Retiring"}

API = "openai-completions"

_FETCH_UA = "nova-ai-model-data/0.1 (+https://github.com/RNA-Nova/nova)"
_FETCH_ATTEMPT_TIMEOUT_S = 4.0
_FETCH_MAX_RETRIES = 2
_FETCH_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

# ---------------------------------------------------------------------------
# provider 常量与修正层（人只维护这里）
# ---------------------------------------------------------------------------

PROVIDERS: Dict[str, Dict[str, str]] = {
    "kimi-coding": {
        "module": "kimi_coding",
        "base_url": "https://api.kimi.com/coding/v1",
        "prefix": "KIMI_CODING",
        "label": "Kimi Coding",
    },
    "moonshotai": {
        "module": "moonshotai",
        "base_url": "https://api.moonshot.ai/v1",
        "prefix": "MOONSHOTAI",
        "label": "Moonshot AI",
    },
    "moonshotai-cn": {
        "module": "moonshotai_cn",
        "base_url": "https://api.moonshot.cn/v1",
        "prefix": "MOONSHOTAI_CN",
        "label": "Moonshot AI CN",
    },
    "volcengine": {
        "module": "volcengine",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3/",
        "prefix": "VOLCENGINE",
        "label": "Volcengine",
    },
}

PROVIDER_COMPAT_DEFAULTS: Dict[str, Dict[str, Any]] = {
    # Moonshot 全系：deepseek 式思考回放，且不接受 store/developer/effort/strict
    "moonshotai": {
        "thinking_format": "deepseek",
        "supports_store": False,
        "supports_developer_role": False,
        "supports_reasoning_effort": False,
        "supports_strict_mode": False,
        "max_tokens_field": "max_tokens",
    },
    "moonshotai-cn": {
        "thinking_format": "deepseek",
        "supports_store": False,
        "supports_developer_role": False,
        "supports_reasoning_effort": False,
        "supports_strict_mode": False,
        "max_tokens_field": "max_tokens",
    },
}

# 修正继承：别名 provider 复用源 provider 的模型级修正
# （moonshotai-cn 目录历史上派生自 moonshotai，仅 id/base_url 不同）
CORRECTION_ALIASES: Dict[str, str] = {"moonshotai-cn": "moonshotai"}

# 模型级修正：glob 模式匹配模型 id（厂商 API 与 models.dev 都不提供的知识）
MODEL_CORRECTIONS: Dict[Tuple[str, str], Dict[str, Any]] = {
    # ---- moonshotai：以真实 API 为准的思考级别表 ----
    ("moonshotai", "kimi-k2.7-code"): {"thinking_level_map": {"off": None}},
    ("moonshotai", "kimi-k2.7-code-highspeed"): {"thinking_level_map": {"off": None}},
    ("moonshotai", "kimi-k3"): {
        # k3 当前支持 low/high/max（TS 静态目录滞后，以真实 API 为准）
        "thinking_level_map": {
            "off": None,
            "minimal": None,
            "low": "low",
            "medium": None,
            "high": "high",
            "xhigh": None,
            "max": "max",
        },
        "compat": {
            "requires_reasoning_content_on_assistant_messages": True,
            "deferred_tools_mode": "kimi",
        },
    },
    # ---- volcengine：deepseek 系思考映射与回放语义 ----
    ("volcengine", "deepseek-v4-*"): {
        "thinking_level_map": {
            "minimal": None,
            "low": None,
            "medium": None,
            "high": "high",
            "max": "max",
        },
        "compat": {
            "requires_reasoning_content_on_assistant_messages": True,
            "thinking_format": "deepseek",
        },
    },
}

class ModelCostFields(TypedDict, total=False):
    """模型种子数据的 cost 形状。"""

    input: float
    output: float
    cache_read: float
    cache_write: float


class ModelFields(TypedDict, total=False):
    """模型种子数据的形状（即 ``Model`` 构造字段——AGENTS.md 规则 10：声明
    不校验；``Model(**fields)`` 在目录边界一次性校验）。"""

    id: str
    name: str
    reasoning: bool
    input_types: List[str]
    cost: ModelCostFields
    context_window: int
    max_tokens: int
    thinking_level_map: Dict[str, Optional[str]]
    headers: Dict[str, str]
    base_url: str
    api: str
    provider: str
    # compat patch——合并进 compat 类的开放子集（键集合由各 compat 类定义）
    compat: Dict[str, Any]


# 钉住的整模型（数据源缺失，但端点实测在售——对齐 TS "authoritative values" 模式）
PINNED_MODELS: Dict[str, Dict[str, ModelFields]] = {
    "volcengine": {
        "deepseek-v4-flash-260425": {
            "id": "deepseek-v4-flash-260425",
            "name": "Deepseek-V4-Flash",
            "reasoning": True,
            "input_types": ["text"],
            "cost": {"input": 1, "output": 2, "cache_read": 0.2, "cache_write": 0.0},
            "context_window": 1048576,
            "max_tokens": 393216,
        },
        "deepseek-v4-pro-260425": {
            "id": "deepseek-v4-pro-260425",
            "name": "Deepseek-V4-Pro",
            "reasoning": True,
            "input_types": ["text"],
            "cost": {"input": 12, "output": 24, "cache_read": 1, "cache_write": 0.0},
            "context_window": 1048576,
            "max_tokens": 393216,
        },
    }
}

# kimi-coding：订阅制，models.dev 无此 provider——整家手写权威块。
# 零成本是订阅语义（对齐 TS KIMI_CODING_IMPLIED_COSTS 注释）。
KIMI_CODING_SOURCE: Dict[str, ModelFields] = {
    "k2p7": {
        "name": "Kimi K2.7 Code",
        "reasoning": True,
        "thinking_level_map": {
            "off": None,
            "minimal": "low",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "high",
            "max": "max",
        },
        "input_types": ["text", "image"],
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 262144,
        "max_tokens": 32768,
        "headers": {"User-Agent": "KimiCLI/1.5"},
        "compat": {
            "thinking_format": "openai",
            "supports_reasoning_effort": True,
            "supports_store": False,
            "max_tokens_field": "max_tokens",
            "supports_developer_role": False,
        },
    },
    "k3": {
        "name": "Kimi K3",
        "reasoning": True,
        # k3 当前支持 low/high/max（以真实 API 为准）
        "thinking_level_map": {
            "off": None,
            "minimal": None,
            "low": "low",
            "medium": None,
            "high": "high",
            "xhigh": None,
            "max": "max",
        },
        "input_types": ["text", "image"],
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 1048576,
        "max_tokens": 131072,
        "headers": {"User-Agent": "KimiCLI/1.5"},
        "compat": {
            "thinking_format": "openai",
            "supports_reasoning_effort": True,
            "supports_store": False,
            "max_tokens_field": "max_tokens",
            "supports_developer_role": False,
        },
    },
    "kimi-for-coding": {
        "name": "Kimi For Coding",
        "reasoning": True,
        "thinking_level_map": {
            "off": None,
            "minimal": "low",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "high",
            "max": "max",
        },
        "input_types": ["text", "image"],
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 262144,
        "max_tokens": 32768,
        "headers": {"User-Agent": "KimiCLI/1.5"},
        "compat": {
            "thinking_format": "openai",
            "supports_reasoning_effort": True,
            "supports_store": False,
            "max_tokens_field": "max_tokens",
            "supports_developer_role": False,
        },
    },
    "kimi-for-coding-highspeed": {
        "name": "Kimi For Coding HighSpeed",
        "reasoning": True,
        "thinking_level_map": {
            "off": None,
            "minimal": "low",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "high",
            "max": "max",
        },
        "input_types": ["text", "image"],
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 262144,
        "max_tokens": 32768,
        "headers": {"User-Agent": "KimiCLI/1.5"},
        "compat": {
            "thinking_format": "openai",
            "supports_reasoning_effort": True,
            "supports_store": False,
            "max_tokens_field": "max_tokens",
            "supports_developer_role": False,
        },
    },
    "kimi-k2-thinking": {
        "name": "Kimi K2 Thinking",
        "reasoning": True,
        "thinking_level_map": {
            "off": None,
            "minimal": "low",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "high",
            "max": "max",
        },
        "input_types": ["text"],
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 262144,
        "max_tokens": 32768,
        "headers": {"User-Agent": "KimiCLI/1.5"},
        "compat": {
            "thinking_format": "openai",
            "supports_reasoning_effort": True,
            "supports_store": False,
            "max_tokens_field": "max_tokens",
            "supports_developer_role": False,
        },
    },
}

# ---------------------------------------------------------------------------
# 拉取（构建期与运行时共用；对齐 TS fetchWithRetry 的主干语义）
# ---------------------------------------------------------------------------

_MODELS_DEV_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}
_MODELS_DEV_CACHE_TTL_S = 60.0


def _fetch_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    api_key: Optional[str] = None,
) -> Any:
    """带尝试超时与瞬态重试的 GET（对齐 TS fetchWithRetry 主干）。"""
    import httpx

    request_headers = {
        "accept": "application/json",
        # models.dev 的 CDN 拦截 Python 默认 UA（403）
        "User-Agent": _FETCH_UA,
        **(headers or {}),
    }
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"

    last_error: Optional[Exception] = None
    for attempt in range(_FETCH_MAX_RETRIES + 1):
        try:
            response = httpx.get(
                url,
                headers=request_headers,
                timeout=_FETCH_ATTEMPT_TIMEOUT_S,
            )
            if (
                response.status_code in _FETCH_RETRYABLE_STATUS
                and attempt < _FETCH_MAX_RETRIES
            ):
                last_error = RuntimeError(f"GET {url} returned {response.status_code}")
                time.sleep(min(0.5 * 2**attempt, 2.0))
                continue
            if response.status_code != 200:
                raise RuntimeError(f"GET {url} returned {response.status_code}")
            return response.json()
        except httpx.TimeoutException as error:
            last_error = error
            if attempt >= _FETCH_MAX_RETRIES:
                raise
            time.sleep(min(0.5 * 2**attempt, 2.0))
    raise last_error if last_error else RuntimeError("unreachable")


def fetch_models_dev() -> Dict[str, Any]:
    """拉取 models.dev 全量目录（60 秒进程内去重——一次刷新的多个 provider 共享）。"""
    now = time.monotonic()
    if (
        _MODELS_DEV_CACHE["data"] is not None
        and now - _MODELS_DEV_CACHE["at"] < _MODELS_DEV_CACHE_TTL_S
    ):
        return _MODELS_DEV_CACHE["data"]
    data = _fetch_json(MODELS_DEV_API)
    _MODELS_DEV_CACHE["at"] = now
    _MODELS_DEV_CACHE["data"] = data
    return data


def fetch_ark_status(api_key: Optional[str]) -> Optional[Dict[str, str]]:
    """火山方舟在售状态表：``{模型 id: status}``（仅显式坏状态；无 key 返回 None）。"""
    if not api_key:
        return None
    try:
        payload = _fetch_json(ARK_MODELS_URL, api_key=api_key)
    except Exception:
        return None
    status_map: Dict[str, str] = {}
    for entry in payload.get("data", []):
        status = entry.get("status")
        if status in ARK_BAD_STATUS:
            status_map[entry.get("id", "")] = status
    return status_map


# ---------------------------------------------------------------------------
# 映射与修正
# ---------------------------------------------------------------------------


def models_dev_to_nova(
    provider_id: str,
    entry: Dict[str, Any],
    strict: bool = False,
) -> Optional[ModelFields]:
    """models.dev 模型条目 → nova ``Model`` 字段 dict（缺字段返回 None）。"""
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


def apply_corrections(
    provider_id: str, model_id: str, fields: ModelFields
) -> ModelFields:
    """套用 provider compat 默认 + 模型级修正（glob 匹配，含别名继承）。"""
    compat_defaults = PROVIDER_COMPAT_DEFAULTS.get(provider_id)
    if compat_defaults:
        compat = {**compat_defaults, **(fields.get("compat") or {})}
        fields["compat"] = compat
    candidates = {provider_id, CORRECTION_ALIASES.get(provider_id)}
    for (correction_provider, pattern), override in MODEL_CORRECTIONS.items():
        if correction_provider not in candidates or not fnmatch.fnmatch(
            model_id, pattern
        ):
            continue
        compat_override = override.get("compat")
        if compat_override:
            compat = {**(fields.get("compat") or {}), **compat_override}
            fields["compat"] = compat
        for key, value in override.items():
            if key != "compat":
                fields[key] = value
    return fields


def build_provider_models(
    provider_id: str,
    raw_models: Dict[str, Dict[str, Any]],
    strict: bool = False,
    static: bool = False,
    ark_status: Optional[Dict[str, str]] = None,
) -> Dict[str, ModelFields]:
    """原始条目 → 补全 provider 常量 → 修正层 → 最终 ``Model`` 字段。

    - ``static=True``：条目已是最终字段形态（kimi-coding 手写权威块）；
    - ``ark_status``：火山生命周期过滤（显式 Shutdown/Retiring 剔除）。
    """
    constants = PROVIDERS[provider_id]
    models: Dict[str, ModelFields] = {}

    def _finalize(model_id: str, fields: ModelFields) -> ModelFields:
        merged: Dict[str, Any] = dict(fields)
        merged = dict(
            apply_corrections(provider_id, model_id, cast(ModelFields, merged))
        )
        merged.update(
            {
                "id": model_id,
                "base_url": constants["base_url"],
                "api": API,
                "provider": provider_id,
            }
        )
        return cast(ModelFields, merged)

    for model_id, fields in PINNED_MODELS.get(provider_id, {}).items():
        models[model_id] = _finalize(model_id, fields)

    for model_id, entry in raw_models.items():
        if model_id in models:
            continue
        if ark_status is not None and model_id in ark_status:
            continue
        fields = (
            cast(ModelFields, entry)
            if static
            else models_dev_to_nova(provider_id, entry, strict)
        )
        if fields is None:
            continue
        models[model_id] = _finalize(model_id, fields)
    return models


def source_models_for(provider_id: str) -> Dict[str, Dict[str, Any]]:
    """取某 provider 的原始条目（models.dev 切片；kimi-coding 走手写权威块）。"""
    if provider_id == "kimi-coding":
        return {mid: dict(fields) for mid, fields in KIMI_CODING_SOURCE.items()}
    source = (fetch_models_dev().get(provider_id) or {}).get("models")
    return source if source else {}


__all__ = [
    "API",
    "CORRECTION_ALIASES",
    "KIMI_CODING_SOURCE",
    "MODEL_CORRECTIONS",
    "PINNED_MODELS",
    "PROVIDERS",
    "PROVIDER_COMPAT_DEFAULTS",
    "apply_corrections",
    "build_provider_models",
    "fetch_ark_status",
    "fetch_models_dev",
    "models_dev_to_nova",
    "source_models_for",
]
