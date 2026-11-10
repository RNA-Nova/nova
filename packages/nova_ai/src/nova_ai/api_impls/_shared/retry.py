"""传输层 provider 重试（对齐 TS ``src/utils/provider-retry.ts``）。

复刻 OpenAI/Anthropic SDK 的固定重试策略，但把退避睡眠做成**可被 abort
打断**的——Python 的 openai SDK 与 TS 同病：内建重试计时器无视请求的
AbortSignal。因此调用方必须以 ``max_retries=0`` 构造客户端，并把请求
包进 :func:`retry_provider_request`。服务端要求的退避超过
``max_retry_delay_ms`` 时直接失败（默认 60 秒；置 0 关闭上限）。

取消的执行层归 ``AbortScope``（``nova_async``——调用方把
retry 段整个包进 scope：退避与在飞尝试在 signal 触发时真死），本层只管
重试策略与可中断退避。
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional, TypedDict, TypeVar

import anthropic
import httpx
import httpx2
from nova_async import abortable_sleep, backoff_delay_ms
from nova_protocol import AbortedError, AbortSignal, is_aborted

from ...utils.errors import (
    extract_retry_after_ms,
    get_header,
    headers_of,
    iter_cause_chain,
    status_code_of,
)

T = TypeVar("T")

DEFAULT_MAX_RETRY_DELAY_MS = 60_000

__all__ = ["retry_provider_request"]


def _abort_error() -> AbortedError:
    return AbortedError("Request aborted")


def _is_retryable_provider_error(error: Exception) -> bool:
    """沿 cause 链镜像 OpenAI/Anthropic SDK 的固定重试判定。"""
    # SDK 的包装错误可能把状态码/显式 RetryableError 放在内层；逐层按
    # 离调用方最近的证据裁决（环保护防畸形异常链死循环）。
    for cause in iter_cause_chain(error):
        if isinstance(cause, anthropic.RetryableError):
            return True
        should_retry = get_header(headers_of(cause), "x-should-retry")
        if should_retry == "true":
            return True
        if should_retry == "false":
            return False
        status = status_code_of(cause)
        if status is not None:
            return status == 408 or status == 409 or status == 429 or status >= 500
        if isinstance(cause, (ConnectionError, TimeoutError)):
            return True
        if isinstance(cause, (httpx.TransportError, httpx2.TransportError)):
            return True
    return False


def _validate_server_retry_delay_ms(
    delay_ms: float,
    max_retry_delay_ms: Optional[float],
    error: Exception,
) -> float:
    max_delay = (
        max_retry_delay_ms
        if max_retry_delay_ms is not None
        else DEFAULT_MAX_RETRY_DELAY_MS
    )
    if max_delay > 0 and delay_ms > max_delay:
        raise RuntimeError(
            f"Server requested {int(-(-delay_ms // 1000))}s retry delay "
            f"(max: {int(-(-max_delay // 1000))}s). {error}"
        ) from error  # 补链：原始 429（含 headers/kind）沿 __cause__ 可捞
    return delay_ms


def _get_retry_delay_ms(
    error: Exception, retry_index: int, max_retry_delay_ms: Optional[float]
) -> float:
    # 服务端要求的退避（retry-after 家族头）优先；无法解析时落指数退避
    # （nova_async backoff 机制件——retry_index 0-based 转正 1-indexed；
    # 默认 jitter=0.25 正等价旧 ``* (1 - random()*0.25)`` 下抖）
    delay_ms = extract_retry_after_ms(headers_of(error))
    if delay_ms is not None:
        return _validate_server_retry_delay_ms(delay_ms, max_retry_delay_ms, error)

    return backoff_delay_ms(retry_index + 1, base_ms=500, cap_ms=8000)


class RetryOptions(TypedDict, total=False):
    """重试选项（规则 10 声明；三键全部可选）。"""

    max_retries: int
    max_retry_delay_ms: float
    signal: Optional[AbortSignal]


async def retry_provider_request(
    request: Callable[[], Awaitable[T]],
    options: Optional[RetryOptions] = None,
) -> T:
    """以 SDK 同款策略重试 ``request``，退避可被 ``signal`` 打断。

    options: ``max_retries``（默认 0）/ ``max_retry_delay_ms`` / ``signal``。
    每次重试都是全新的 SDK 请求（X-Stainless-Retry-Count 保持为零）。
    """
    opts = options or {}
    max_retries = opts.get("max_retries") or 0
    signal: Optional[AbortSignal] = opts.get("signal")
    retries_remaining = max_retries

    while True:
        try:
            return await request()
        except Exception as error:
            if isinstance(error, AbortedError):
                raise
            if is_aborted(signal):
                raise _abort_error()
            if retries_remaining <= 0 or not _is_retryable_provider_error(error):
                raise

            retry_index = max_retries - retries_remaining
            retries_remaining -= 1
            await abortable_sleep(
                _get_retry_delay_ms(error, retry_index, opts.get("max_retry_delay_ms")),
                signal,
            )
