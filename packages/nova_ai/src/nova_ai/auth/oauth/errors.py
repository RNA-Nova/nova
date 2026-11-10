"""OAuth 流程错误（用户登录面——自声明 kind，归一阶梯第 1 级消费）。"""

from typing import Optional

import httpx
from nova_protocol import NovaErrorKind, redact_secrets
from pydantic import ValidationError


def oauth_http_kind(status_code: int) -> NovaErrorKind:
    """OAuth 端点 HTTP 状态 → 标准诊断分类；只有凭据失败才要求重登录。"""
    if status_code in (401, 403):
        return NovaErrorKind.UNAUTHORIZED
    if status_code == 408:
        return NovaErrorKind.REQUEST_TIMEOUT
    if status_code in (409, 529):
        return NovaErrorKind.OVERLOADED
    if status_code == 429:
        return NovaErrorKind.RATE_LIMITED
    if status_code >= 500:
        return NovaErrorKind.UPSTREAM_ERROR
    return NovaErrorKind.BAD_REQUEST


class OAuthFlowError(RuntimeError):
    """OAuth 登录/刷新流程错误。

    ``nova_error_kind`` 自声明分类（**可选**——不传则归一按 ``status_code``
    的标准映射判定，429→限流、5xx→对端故障）；``status_code`` 透传 HTTP
    状态给归一与遥测（``NormalizedError.http_status`` 的取数面）。
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Optional[NovaErrorKind] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        if kind is not None:
            self.nova_error_kind = kind
        if status_code is not None:
            self.status_code = status_code


def validation_message(what: str, error: ValidationError) -> str:
    """响应校验失败的消息——只列字段名与期望形状，**绝不回显响应体**。

    安全纪律（codex serde 对位）：只取 ``error.errors()`` 的 ``loc``/``msg``
    ——pydantic 的 ``str(err)`` 会经 ``input`` 键回显输入值（token 端点的
    响应体可能含活凭据），这是它与 serde 的坑差别，本函数就是绕开它的
    单点。字段级原因（"Field required"/"Input should be ..."）不含输入值，
    可安全引用。
    """
    details = "; ".join(
        f"{'.'.join(str(p) for p in entry['loc'])}: {entry['msg']}"
        for entry in error.errors()
    )
    return f"{what} response missing/invalid fields: {details}"


def validation_error(what: str, error: ValidationError) -> OAuthFlowError:
    """``validation_message`` 的异常包装（OAuth 端点响应形状非法）。"""
    return OAuthFlowError(
        validation_message(what, error), kind=NovaErrorKind.UPSTREAM_ERROR
    )


def response_error_detail(response: httpx.Response) -> str:
    """错误状态码响应的安全细节——只提取结构化错误字段，不回显原始 body。

    codex ``parse_token_endpoint_error`` 对位：JSON 错误体取
    ``error_description`` / ``error``（含 ``error.code`` 对象形态）；非 JSON
    载荷（WAF/网关 HTML、空体）落到 ``reason_phrase``。原始响应体永不进消息
    ——上游文本内容不可控（可能夹带凭据或任意 HTML）。
    """
    try:
        data = response.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        description = data.get("error_description")
        if isinstance(description, str) and description:
            # 服务器控制的自由文本——出口前清洗（可夹带任意内容）
            return redact_secrets(description)
        error = data.get("error")
        if isinstance(error, str) and error:
            return redact_secrets(error)
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code:
                return redact_secrets(code)
    return response.reason_phrase or f"HTTP {response.status_code}"
