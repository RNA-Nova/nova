"""Provider attribution headers.

为特定 LLM provider 自动附加 attribution / billing / session 头，
避免用户手动在 ``models.json`` 里配置。行为对齐 TypeScript 端的
``provider-attribution.ts``。

当前支持的 provider：
- OpenRouter
- NVIDIA NIM
- Cloudflare AI Gateway / Workers AI
- OpenCode
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from nova_ai import Model

from nova_harness.core.utils.telemetry import is_install_telemetry_enabled

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

_OPENROUTER_HOST = "openrouter.ai"
_NVIDIA_NIM_HOST = "integrate.api.nvidia.com"
_CLOUDFLARE_API_HOST = "api.cloudflare.com"
_CLOUDFLARE_AI_GATEWAY_HOST = "gateway.ai.cloudflare.com"
_OPENCODE_HOST = "opencode.ai"


def _hostname(base_url: str) -> str:
    try:
        return urlparse(base_url).hostname or ""
    except Exception:
        return ""


def _matches_host(base_url: str, expected_host: str) -> bool:
    return _hostname(base_url) == expected_host


def _is_openrouter_model(model: Model) -> bool:
    return model.provider == "openrouter" or _matches_host(
        model.base_url, _OPENROUTER_HOST
    )


def _is_nvidia_nim_model(model: Model) -> bool:
    return model.provider == "nvidia" or _matches_host(model.base_url, _NVIDIA_NIM_HOST)


def _is_cloudflare_model(model: Model) -> bool:
    return (
        model.provider in ("cloudflare-workers-ai", "cloudflare-ai-gateway")
        or _matches_host(model.base_url, _CLOUDFLARE_API_HOST)
        or _matches_host(model.base_url, _CLOUDFLARE_AI_GATEWAY_HOST)
    )


def _is_opencode_model(model: Model) -> bool:
    return model.provider in ("opencode", "opencode-go") or _matches_host(
        model.base_url, _OPENCODE_HOST
    )


# ---------------------------------------------------------------------------
# Header builders
# ---------------------------------------------------------------------------


def _get_default_attribution_headers(
    model: Model,
    settings_manager: Any,
) -> Optional[Dict[str, str]]:
    """根据模型 provider/base_url 返回默认 attribution headers。

    与 TypeScript 端对齐：只有在安装遥测开启时才附加这些 headers，
    避免在用户关闭遥测时仍然向 provider 暴露标识信息。
    """
    if not is_install_telemetry_enabled(settings_manager):
        return None

    if _is_openrouter_model(model):
        return {
            "HTTP-Referer": "https://nova.dev",
            "X-OpenRouter-Title": "nova",
            "X-OpenRouter-Categories": "cli-agent",
        }

    if _is_nvidia_nim_model(model):
        return {
            "X-BILLING-INVOKE-ORIGIN": "Nova",
        }

    if _is_cloudflare_model(model):
        return {
            "User-Agent": "nova-coding-agent",
        }

    return None


def _get_session_headers(
    model: Model, session_id: Optional[str]
) -> Optional[Dict[str, str]]:
    """OpenCode 需要 session/client 头。"""
    if not session_id:
        return None
    if not _is_opencode_model(model):
        return None
    return {
        "x-opencode-session": session_id,
        "x-opencode-client": "nova",
    }


def merge_provider_attribution_headers(
    model: Model,
    settings_manager: Any,
    session_id: Optional[str] = None,
    *header_sources: Optional[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    """合并 provider attribution headers。

    与 TypeScript 端 ``mergeProviderAttributionHeaders`` 对齐：

    - OpenCode session headers 只要提供 ``session_id`` 且匹配 provider 就附加。
    - 默认 provider attribution headers 仅在安装遥测开启时附加。
    - 调用方传入的 header sources 优先级最高，可覆盖默认值。

    Args:
        model: 当前使用的模型。
        settings_manager: 提供 ``get_enable_install_telemetry()`` 的设置管理器。
        session_id: 可选会话 ID，用于 OpenCode session headers。
        *header_sources: 调用方传入的额外 headers（如 ``options.headers``、
            ``model.headers`` 等），后传入的覆盖先传入的。

    返回 ``None`` 表示没有需要附加的 header。
    """
    merged: Dict[str, str] = {}

    session_headers = _get_session_headers(model, session_id)
    if session_headers:
        merged.update(session_headers)

    default_headers = _get_default_attribution_headers(model, settings_manager)
    if default_headers:
        merged.update(default_headers)

    for headers in header_sources:
        if headers:
            merged.update(headers)

    return merged if merged else None


__all__ = ["merge_provider_attribution_headers"]
