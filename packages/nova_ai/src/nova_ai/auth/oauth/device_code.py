"""Device Code 轮询。

对齐 TypeScript ``src/auth/oauth/device-code.ts``：按 interval 轮询 token，
支持 slow_down、超时、signal 取消。
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, Literal, Optional, TypeVar

from nova_protocol import AbortSignal

T = TypeVar("T")

DeviceCodePollStatus = Literal["pending", "slow_down", "failed", "complete"]
"""单次轮询状态：继续等 / 降速继续 / 失败终态 / 成功终态。"""


@dataclass(frozen=True, kw_only=True)
class DeviceCodePollResult(Generic[T]):
    """单次轮询结果（不可变值对象——规则 5）。

    - ``value``：``status == "complete"`` 时的终值；其余状态为 ``None``；
    - ``message``：``status == "failed"`` 时的错误描述；
    - ``interval_seconds``：``status == "slow_down"`` 时服务器要求的
      新轮询间隔；``None`` = 走默认退避。
    """

    status: DeviceCodePollStatus
    value: Optional[T] = None
    message: Optional[str] = None
    interval_seconds: Optional[float] = None


@dataclass(frozen=True, kw_only=True)
class DeviceCodePollOptions(Generic[T]):
    """轮询配置（持 ``poll`` Callable 与 ``signal``——规则 4，不进 Pydantic）。

    - ``interval_seconds``：初始轮询间隔（秒）；``None`` = 默认 5 秒；
    - ``expires_in_seconds``：整个流程的超时（秒）；``None`` = 不超时；
    - ``wait_before_first_poll``：首次轮询前先等一个间隔；
    - ``signal``：取消信号；中断以 ``asyncio.CancelledError`` 收场。
    """

    poll: Callable[[], Awaitable[DeviceCodePollResult[T]]]
    interval_seconds: Optional[float] = None
    expires_in_seconds: Optional[float] = None
    wait_before_first_poll: bool = False
    signal: Optional[AbortSignal] = None


_MINIMUM_INTERVAL_MS = 1000
_DEFAULT_POLL_INTERVAL_SECONDS = 5
_SLOW_DOWN_INCREMENT_MS = 5000
_CANCEL_MESSAGE = "Login cancelled"
_TIMEOUT_MESSAGE = "Device flow timed out"
_SLOW_DOWN_TIMEOUT_MESSAGE = (
    "Device flow timed out after one or more slow_down responses. "
    "This is often caused by clock drift in WSL or VM environments. "
    "Please sync or restart the VM clock and try again."
)


def _is_aborted(signal: Optional[AbortSignal]) -> bool:
    return signal is not None and signal.aborted


async def _abortable_sleep(ms: float, signal: Optional[AbortSignal]) -> None:
    if _is_aborted(signal):
        raise asyncio.CancelledError(_CANCEL_MESSAGE)

    done_event = asyncio.Event()

    def _on_abort(_signal: AbortSignal) -> None:
        done_event.set()

    if signal is not None:
        signal.add_event_listener(_on_abort)

    try:
        try:
            async with asyncio.timeout(ms / 1000):
                await done_event.wait()
        except TimeoutError:
            pass
        if _is_aborted(signal):
            raise asyncio.CancelledError(_CANCEL_MESSAGE)
    finally:
        if signal is not None:
            signal.remove_event_listener(_on_abort)


async def poll_oauth_device_code_flow(options: DeviceCodePollOptions[T]) -> T:
    """按 RFC 8628 轮询 device code。"""

    def _now_ms() -> float:
        return time.time() * 1000

    deadline = (
        _now_ms() + options.expires_in_seconds * 1000
        if options.expires_in_seconds is not None
        else float("inf")
    )
    interval_ms = max(
        _MINIMUM_INTERVAL_MS,
        (options.interval_seconds or _DEFAULT_POLL_INTERVAL_SECONDS) * 1000,
    )

    slow_down_count = 0
    if options.wait_before_first_poll:
        remaining_ms = deadline - _now_ms()
        if remaining_ms > 0:
            await _abortable_sleep(min(interval_ms, remaining_ms), options.signal)

    while _now_ms() < deadline:
        if _is_aborted(options.signal):
            raise asyncio.CancelledError(_CANCEL_MESSAGE)

        result = await options.poll()
        if result.status == "complete":
            if result.value is None:
                raise RuntimeError("Device code poll returned complete without value")
            return result.value
        if result.status == "failed":
            raise RuntimeError(result.message or "Device code flow failed")
        if result.status == "slow_down":
            slow_down_count += 1
            if result.interval_seconds is not None and result.interval_seconds > 0:
                interval_ms = max(_MINIMUM_INTERVAL_MS, result.interval_seconds * 1000)
            else:
                interval_ms += _SLOW_DOWN_INCREMENT_MS

        remaining_ms = deadline - _now_ms()
        if remaining_ms <= 0:
            break
        await _abortable_sleep(min(interval_ms, remaining_ms), options.signal)

    raise TimeoutError(
        _SLOW_DOWN_TIMEOUT_MESSAGE if slow_down_count > 0 else _TIMEOUT_MESSAGE
    )


__all__ = [
    "DeviceCodePollOptions",
    "DeviceCodePollResult",
    "DeviceCodePollStatus",
    "poll_oauth_device_code_flow",
]
