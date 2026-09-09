"""传输层 provider 重试（对齐 TS ``src/utils/provider-retry.ts``）。

复刻 OpenAI/Anthropic SDK 的固定重试策略，但把退避睡眠做成**可被 abort
打断**的——Python 的 openai SDK 与 TS 同病：内建重试计时器无视请求的
AbortSignal。因此调用方必须以 ``max_retries=0`` 构造客户端，并把请求
包进 :func:`retry_provider_request`。服务端要求的退避超过
``max_retry_delay_ms`` 时直接失败（默认 60 秒；置 0 关闭上限）。
"""

from __future__ import annotations

import asyncio
import random
import time
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Optional, TypeVar, TypedDict

from ...signal import AbortedError, AbortSignal

T = TypeVar("T")

DEFAULT_MAX_RETRY_DELAY_MS = 60_000

__all__ = ["retry_provider_request"]


def _abort_error() -> AbortedError:
    return AbortedError("Request aborted")


def _header(headers: Any, name: str) -> Optional[str]:
    """从 openai SDK 的 httpx Headers / dict 形态里取头（大小写不敏感）。"""
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_retryable_provider_error(error: Exception) -> bool:
    """镜像 OpenAI/Anthropic SDK 的固定重试判定；SDK 升级时需同步复核。"""
    should_retry = _header(getattr(error, "headers", None), "x-should-retry")
    if should_retry == "true":
        return True
    if should_retry == "false":
        return False

    status = getattr(error, "status_code", None)
    if status is not None:
        return status == 408 or status == 409 or status == 429 or status >= 500
    # 无状态码：仅连接层/超时错误可重试——编程错误（TypeError 等）
    # 也无状态码，一律重试会把失败掩盖成慢失败
    return isinstance(error, (ConnectionError, TimeoutError, asyncio.TimeoutError))


def _validate_server_retry_delay_ms(
    delay_ms: float,
    max_retry_delay_ms: Optional[float],
    provider_error_message: str,
) -> float:
    max_delay = (
        max_retry_delay_ms
        if max_retry_delay_ms is not None
        else DEFAULT_MAX_RETRY_DELAY_MS
    )
    if max_delay > 0 and delay_ms > max_delay:
        raise RuntimeError(
            f"Server requested {int(-(-delay_ms // 1000))}s retry delay "
            f"(max: {int(-(-max_delay // 1000))}s). {provider_error_message}"
        )
    return delay_ms


def _get_retry_delay_ms(
    error: Exception, retry_index: int, max_retry_delay_ms: Optional[float]
) -> float:
    retry_after_ms = _header(getattr(error, "headers", None), "retry-after-ms")
    if retry_after_ms:
        try:
            return _validate_server_retry_delay_ms(
                float(retry_after_ms), max_retry_delay_ms, str(error)
            )
        except ValueError:
            pass

    retry_after = _header(getattr(error, "headers", None), "retry-after")
    if retry_after:
        try:
            delay_ms = float(retry_after) * 1000
        except ValueError:
            try:
                delay_ms = (
                    parsedate_to_datetime(retry_after).timestamp() - time.time()
                ) * 1000
            except Exception:
                delay_ms = 0.0
        return _validate_server_retry_delay_ms(delay_ms, max_retry_delay_ms, str(error))

    exponential_delay = min(0.5 * 2**retry_index, 8) * 1000
    return exponential_delay * (1 - random.random() * 0.25)


async def _abortable_sleep(ms: float, signal: Optional[AbortSignal]) -> None:
    """至多睡 ``ms`` 毫秒；期间 signal 中断则抛 AbortedError（asyncio.timeout 惯用法）。"""
    if signal is not None and signal.aborted:
        raise _abort_error()
    seconds = max(0.0, ms / 1000)
    if signal is None:
        await asyncio.sleep(seconds)
        return
    try:
        async with asyncio.timeout(seconds):
            await signal.wait()
    except TimeoutError:
        return  # 睡满正常返回
    except asyncio.CancelledError:
        # asyncio.timeout 以取消实现超时；仅在非超时取消时外传
        if signal.aborted:
            raise _abort_error()
        raise


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
            if signal is not None and signal.aborted:
                raise _abort_error()
            if retries_remaining <= 0 or not _is_retryable_provider_error(error):
                raise

            retry_index = max_retries - retries_remaining
            retries_remaining -= 1
            await _abortable_sleep(
                _get_retry_delay_ms(error, retry_index, opts.get("max_retry_delay_ms")),
                signal,
            )
