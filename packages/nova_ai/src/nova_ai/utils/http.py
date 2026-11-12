"""HTTP 薄封装原语（对齐 pi ``fetchWithRetry`` / codex ``util::backoff`` 的尺度）。

两个原语，共享策略常量（UA / 尝试超时 / 重试集合）：

- ``request_with_abort``：async 单次请求，**在飞真杀**——``await_with_abort``
  （``AbortScope``）包住请求，举旗即杀断在飞连接抛 ``AbortedError``；
- ``fetch_json``：async GET + 重试 + 非 200 raise + ``.json()``
  （运行期目录拉取；每次尝试走 ``request_with_abort``，尝试之间检查 abort
  并可中断退避）。构建期单次 GET 归 ``nova_catalog``，不反向复用本模块。

传输错误统一包装为 ``RuntimeError``——调用方按 RuntimeError 识别传输
失败并重试，无需感知 httpx 异常族。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from nova_async import abortable_sleep, await_with_abort, backoff_delay_ms
from nova_protocol import AbortedError, AbortSignal, is_aborted

_FETCH_UA = "nova-ai-model-data/0.1 (+https://github.com/RNA-Nova/nova)"
_ATTEMPT_TIMEOUT_S = 4.0
_MAX_RETRIES = 2
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

__all__ = [
    "HttpFetchError",
    "fetch_json",
    "filter_headers_for_hook",
    "get_with_retry",
    "json_headers",
    "request_with_abort",
]

# 进钩子事件（on_response/ProviderResponse）前的响应头黑名单（小写比对）——
# 凭据/会话类头永不出进程内钩子的手（security：出口清洗纪律——钩子即出口，
# 钩子的消费方（日志/诊断/第三方扩展）不该见到它们）。
_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "proxy-authenticate",
        "x-api-key",
        "set-cookie",
        "cookie",
        "www-authenticate",
    }
)


def filter_headers_for_hook(headers: Dict[str, str]) -> Dict[str, str]:
    """响应头进钩子事件前的黑名单过滤（大小写不敏感，返回新 dict）。"""
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _SENSITIVE_HEADER_NAMES
    }


class HttpFetchError(RuntimeError):
    """``fetch_json`` 的 HTTP 层错误（非 2xx）——``status_code`` 为一等属性。

    归一阶梯的结构化状态码级直接消费（``status_code_of`` 读属性），不靠
    文案解析——"GET … returned 429" 式文案嵌码已废弃。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        # 重试与错误归一需要 Retry-After / x-should-retry 等响应元数据。
        self.headers = dict(headers) if headers is not None else None


def json_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """目录 JSON 拉取的基础头（accept + UA；``extra`` 并入，如 if-none-match）。

    models.dev 的 CDN 拦截 Python 默认 UA（403）——UA 是目录域共享策略，
    唯一来源在这里。
    """
    headers = {
        "accept": "application/json",
        "User-Agent": _FETCH_UA,
    }
    if extra:
        headers.update(extra)
    return headers


async def request_with_abort(
    method: str,
    url: str,
    *,
    signal: Optional[AbortSignal] = None,
    data: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float | httpx.Timeout = _ATTEMPT_TIMEOUT_S,
) -> httpx.Response:
    """发送一次 HTTP 请求；``signal`` 中断即取消在飞请求并抛 ``AbortedError``。

    传输错误（连接失败/超时等）统一包装为 ``RuntimeError``；响应交给调用方
    判定状态码（需要读错误体/分状态收场的场景，如 OAuth、managed 端点）。
    """
    if is_aborted(signal):
        raise AbortedError("The operation was aborted")

    client = httpx.AsyncClient()
    try:
        # 仅请求等待进入取消作用域；client 的异步关闭必须在 scope 外，
        # 否则 level-triggered 取消会让 __aexit__/close 的 await 立即再死。
        return await await_with_abort(
            client.request(
                method,
                url,
                data=data,
                json=json_body,
                headers=headers,
                timeout=timeout,
            ),
            signal,
        )
    except httpx.TransportError as error:
        raise RuntimeError(f"HTTP {method} {url} failed: {error}") from error
    finally:
        await client.aclose()


async def get_with_retry(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    signal: Optional[AbortSignal] = None,
) -> httpx.Response:
    """GET + 有界重试，保留最终响应状态供调用方分流（pi fetchWithRetry）。

    传输失败及 408/425/429/500/502/503/504 至多额外重试两次；404、501、
    304 等非重试状态原样返回。abort 在请求与退避期间都直通。
    """
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = await request_with_abort(
                "GET", url, headers=headers, signal=signal, timeout=_ATTEMPT_TIMEOUT_S
            )
        except RuntimeError:
            if attempt >= _MAX_RETRIES:
                raise
        else:
            if response.status_code not in _RETRYABLE_STATUS or attempt >= _MAX_RETRIES:
                return response
        await abortable_sleep(
            backoff_delay_ms(attempt + 1, base_ms=500, cap_ms=2000, jitter=0),
            signal,
        )
    raise RuntimeError("unreachable")


async def fetch_json(
    url: str,
    *,
    api_key: Optional[str] = None,
    signal: Optional[AbortSignal] = None,
) -> Any:
    """重试 GET + 非 200 raise + ``.json()``；HTTP 错误保留响应头。"""
    headers = json_headers({"Authorization": f"Bearer {api_key}"} if api_key else None)
    response = await get_with_retry(url, headers=headers, signal=signal)
    if response.status_code != 200:
        raise HttpFetchError(
            f"GET {url} returned {response.status_code}",
            status_code=response.status_code,
            headers=dict(response.headers),
        )
    return response.json()
