"""Device Code 轮询。

对齐 TypeScript ``src/auth/oauth/device-code.ts``：按 interval 轮询 token，
支持 slow_down、超时、signal 取消。
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, Literal, Optional, TypeVar

from ...signal import AbortSignal

T = TypeVar("T")

DeviceCodePollStatus = Literal["pending", "slow_down", "failed", "complete"]


@dataclass(frozen=True, kw_only=True)
class DeviceCodePollResult(Generic[T]):
    """单次轮询结果（不可变值对象——规则 5）。"""

    status: DeviceCodePollStatus
    value: Optional[T] = None
    message: Optional[str] = None
    interval_seconds: Optional[float] = None


class DeviceCodePollOptions(Generic[T]):
    """轮询配置（持 ``poll`` Callable 与 ``signal``——规则 4，不冻结/不进 Pydantic）。"""

    def __init__(
        self,
        poll: Callable[[], Awaitable[DeviceCodePollResult[T]]],
        interval_seconds: Optional[float] = None,
        expires_in_seconds: Optional[float] = None,
        wait_before_first_poll: bool = False,
        signal: Optional[AbortSignal] = None,
    ):
        self.poll = poll
        self.interval_seconds = interval_seconds
        self.expires_in_seconds = expires_in_seconds
        self.wait_before_first_poll = wait_before_first_poll
        self.signal = signal


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
    except asyncio.TimeoutError:
        pass
    finally:
        if signal is not None:
            signal.remove_event_listener(_on_abort)


async def poll_oauth_device_code_flow(options: DeviceCodePollOptions[T]) -> T:
    """按 RFC 8628 轮询 device code。"""
    now_ms = lambda: time.time() * 1000
    deadline = (
        now_ms() + options.expires_in_seconds * 1000
        if options.expires_in_seconds is not None
        else float("inf")
    )
    interval_ms = max(
        _MINIMUM_INTERVAL_MS,
        (options.interval_seconds or _DEFAULT_POLL_INTERVAL_SECONDS) * 1000,
    )

    slow_down_count = 0
    if options.wait_before_first_poll:
        remaining_ms = deadline - now_ms()
        if remaining_ms > 0:
            await _abortable_sleep(min(interval_ms, remaining_ms), options.signal)

    while now_ms() < deadline:
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

        remaining_ms = deadline - now_ms()
        if remaining_ms <= 0:
            break
        await _abortable_sleep(min(interval_ms, remaining_ms), options.signal)

    raise TimeoutError(
        _SLOW_DOWN_TIMEOUT_MESSAGE if slow_down_count > 0 else _TIMEOUT_MESSAGE
    )


__all__ = [
    "DeviceCodePollOptions",
    "DeviceCodePollResult",
    "poll_oauth_device_code_flow",
]
