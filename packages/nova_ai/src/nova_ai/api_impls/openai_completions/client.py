"""客户端创建（对齐 TS ``createClient`` / ``hasHeader``）。

重试纪律：客户端一律 ``max_retries=0``——SDK 内建重试的退避睡眠无视
abort；真正的重试由 ``_shared/retry.py`` 的 ``retry_provider_request``
在请求层接管（可被 signal 打断）。
"""

from typing import Any, Dict, Optional

from openai import AsyncOpenAI

from ...types.compat import OpenAICompletionsCompat
from ...types.messages import Context
from ...types.model import Model
from .._shared.copilot_headers import (
    build_copilot_dynamic_headers,
    has_copilot_vision_input,
)
from .._shared.user_agent import get_nova_user_agent
from .compat import get_compat
from .options import api


def _has_header(headers: Optional[Dict[str, Optional[str]]], name: str) -> bool:
    """检查 headers 中是否已存在非空指定头部（对齐 TS hasHeader）。"""
    if not headers:
        return False
    expected = name.lower()
    for key, value in headers.items():
        if key.lower() == expected and value is not None and value.strip():
            return True
    return False


def create_client(
    model: Model,
    context: Context,
    api_key: Optional[str] = None,
    options_headers: Optional[Dict[str, Optional[str]]] = None,
    session_id: Optional[str] = None,
    compat: Optional[OpenAICompletionsCompat] = None,
) -> AsyncOpenAI:
    """创建 OpenAI 客户端（对齐 TS createClient）。

    重试纪律：客户端恒 ``max_retries=0``——SDK 内建重试不可被 abort 打断，
    重试归 ``_shared/retry.py`` 的 ``retry_provider_request`` 接管。
    """
    resolved_compat = compat or get_compat(model)

    headers: Dict[str, Optional[str]] = {"User-Agent": get_nova_user_agent()}
    if model.headers:
        headers.update(model.headers)

    if model.provider == "github-copilot":
        has_images = has_copilot_vision_input(context.messages)
        copilot_headers = build_copilot_dynamic_headers(context.messages, has_images)
        headers.update(copilot_headers)

    if session_id and resolved_compat.send_session_affinity_headers:
        fmt = resolved_compat.session_affinity_format or "openai"
        if fmt == "openrouter":
            headers["x-session-id"] = session_id
        else:
            if fmt == "openai":
                headers["session_id"] = session_id
            headers["x-client-request-id"] = session_id
            headers["x-session-affinity"] = session_id

    if options_headers:
        headers.update(options_headers)

    if (
        not api_key
        and not _has_header(headers, "authorization")
        and not _has_header(headers, "cf-aig-authorization")
    ):
        # 对齐 TS getClientApiKey：协议层不读环境变量，api key 必须由上游
        # （Models.applyAuth / 调用方 options）注入；headers 自带 auth 时经
        # client_kwargs 的 "unused" 占位放行。
        raise ValueError(f"No API key for provider: {model.provider}")

    # Cloudflare AI Gateway 特殊鉴权头部
    if model.provider == "cloudflare-ai-gateway":
        default_headers: Dict[str, Optional[str]] = {
            **headers,
            "Authorization": headers.get("Authorization") or "",
            "cf-aig-authorization": f"Bearer {api_key}",
        }
    else:
        default_headers = headers

    client_kwargs: Dict[str, Any] = {
        "api_key": api_key or "unused",
        "base_url": model.base_url,
        "default_headers": {k: v for k, v in default_headers.items() if v is not None},
        # 恒为 0：SDK 内建重试不可被 abort 打断，重试归 retry_provider_request
        "max_retries": 0,
    }

    return AsyncOpenAI(**client_kwargs)
