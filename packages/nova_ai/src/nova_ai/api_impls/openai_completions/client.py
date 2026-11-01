"""客户端创建（对齐 TS ``createClient`` / ``hasHeader``）。

重试纪律：客户端一律 ``max_retries=0``——SDK 内建重试的退避睡眠无视
abort；真正的重试由 ``_shared/retry.py`` 的 ``retry_provider_request``
在请求层接管（可被 signal 打断）。
"""

from typing import Optional

from nova_protocol import Context, Model, OpenAICompletionsCompat, ProviderHeaders
from openai import AsyncOpenAI

from ...utils.errors import LoginRequiredError
from .._shared.copilot_headers import (
    build_copilot_dynamic_headers,
    has_copilot_vision_input,
)
from .._shared.user_agent import get_nova_user_agent
from .compat import get_compat
from .options import api


def _get_header_value(headers: Optional[ProviderHeaders], name: str) -> Optional[str]:
    """大小写不敏感地取头部值（值缺失/空串视为不存在）。"""
    if not headers:
        return None
    expected = name.lower()
    for key, value in headers.items():
        if key.lower() == expected and value is not None and value.strip():
            return value
    return None


def _has_header(headers: Optional[ProviderHeaders], name: str) -> bool:
    """检查 headers 中是否已存在非空指定头部（对齐 TS hasHeader）。"""
    return _get_header_value(headers, name) is not None


def create_client(
    model: Model,
    context: Context,
    api_key: Optional[str] = None,
    options_headers: Optional[ProviderHeaders] = None,
    session_id: Optional[str] = None,
    compat: Optional[OpenAICompletionsCompat] = None,
) -> AsyncOpenAI:
    """创建 OpenAI 客户端（对齐 TS createClient）。

    重试纪律：客户端恒 ``max_retries=0``——SDK 内建重试不可被 abort 打断，
    重试归 ``_shared/retry.py`` 的 ``retry_provider_request`` 接管。
    """
    resolved_compat = compat or get_compat(model)

    headers: ProviderHeaders = {"User-Agent": get_nova_user_agent()}
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
        # （ModelsManager.applyAuth / 调用方 options）注入；headers 自带 auth 时经
        # client_kwargs 的 "unused" 占位放行。
        raise LoginRequiredError(model.provider)

    # Cloudflare AI Gateway 特殊鉴权头部（对齐 TS getClientApiKey）：
    # 双头形态——Authorization 置空占位，真实 key 走 cf-aig-authorization。
    # 仅在 api_key 非空时构造；为空说明调用方自带鉴权头（上方放行路径已
    # 保证存在），原样保留，不得覆盖。
    if model.provider == "cloudflare-ai-gateway" and api_key:
        default_headers: ProviderHeaders = {
            **headers,
            "Authorization": _get_header_value(headers, "authorization") or "",
            "cf-aig-authorization": f"Bearer {api_key}",
        }
    else:
        default_headers = headers

    return AsyncOpenAI(
        api_key=api_key or "unused",
        base_url=model.base_url,
        default_headers={k: v for k, v in default_headers.items() if v is not None},
        # 恒为 0：SDK 内建重试不可被 abort 打断，重试归 retry_provider_request
        max_retries=0,
    )
