"""HTTP 薄封装原语（对齐 pi ``fetchWithRetry`` / codex ``util::backoff`` 的尺度）。

两个原语，共享策略常量（UA / 尝试超时 / 重试集合）：

- ``request_with_abort``：async 单次请求，**在飞真杀**——``await_with_abort``
  （``AbortScope``）包住请求，举旗即杀断在飞连接抛 ``AbortedError``；
- ``fetch_json``：async GET + 重试 + 非 200 raise + ``.json()``
  （目录拉取/构建脚本共用；每次尝试走 ``request_with_abort``，尝试之间
  检查 abort 并可中断退避）。

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

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


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
    timeout: float = _ATTEMPT_TIMEOUT_S,
) -> httpx.Response:
    """发送一次 HTTP 请求；``signal`` 中断即取消在飞请求并抛 ``AbortedError``。

    传输错误（连接失败/超时等）统一包装为 ``RuntimeError``；响应交给调用方
    判定状态码（需要读错误体/分状态收场的场景，如 OAuth、managed 端点）。
    """
    if is_aborted(signal):
        raise AbortedError("The operation was aborted")

    async def _do_request() -> httpx.Response:
        try:
            async with httpx.AsyncClient() as client:
                return await client.request(
                    method,
                    url,
                    data=data,
                    json=json_body,
                    headers=headers,
                    timeout=timeout,
                )
        except httpx.TransportError as error:
            raise RuntimeError(f"HTTP {method} {url} failed: {error}") from error

    return await await_with_abort(_do_request(), signal)


async def fetch_json(
    url: str,
    *,
    api_key: Optional[str] = None,
    signal: Optional[AbortSignal] = None,
) -> Any:
    """GET + 有界重试 + 非 200 raise + ``.json()``（对齐 pi fetchWithRetry 主干）。

    每次尝试单发 4s 超时，可重试状态码（408/425/429/5xx）与传输错误重试
    至多 2 次（指数退避，退避可中断）；abort 在每次尝试前后与退避期间生效。
    """
    headers = json_headers({"Authorization": f"Bearer {api_key}"} if api_key else None)

    last_error: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = await request_with_abort(
                "GET", url, headers=headers, signal=signal, timeout=_ATTEMPT_TIMEOUT_S
            )
        except RuntimeError as error:
            # 传输失败重试（对齐 pi：transport failures are retryable）
            last_error = error
            if attempt >= _MAX_RETRIES:
                raise
            # 退避机制件（nova_async backoff）：attempt 0-based 转正 1-indexed；
            # jitter=0 保持现行为（旧公式本无抖动）
            await abortable_sleep(
                backoff_delay_ms(attempt + 1, base_ms=500, cap_ms=2000, jitter=0),
                signal,
            )
            continue
        if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
            last_error = HttpFetchError(
                f"GET {url} returned {response.status_code}",
                status_code=response.status_code,
            )
            await abortable_sleep(
                backoff_delay_ms(attempt + 1, base_ms=500, cap_ms=2000, jitter=0),
                signal,
            )
            continue
        if response.status_code != 200:
            raise HttpFetchError(
                f"GET {url} returned {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()
    raise last_error if last_error else RuntimeError("unreachable")
