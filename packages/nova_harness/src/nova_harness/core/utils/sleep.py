import asyncio
from typing import Optional

from nova_agent import AbortSignal


async def sleep(seconds: float, signal: Optional[AbortSignal] = None) -> None:
    """
    Sleep helper that respects abort signal.

    Args:
        seconds: Duration to sleep in seconds
        signal: Optional AbortSignal to abort the sleep

    Raises:
        RuntimeError: If the signal is already aborted when sleep starts
        RuntimeError: If the signal is aborted during sleep
    """
    if signal is not None and signal.aborted:
        raise RuntimeError("Aborted")

    try:
        await asyncio.wait_for(_sleep_with_abort(seconds, signal), timeout=None)
    except asyncio.CancelledError:
        raise RuntimeError("Aborted") from None


async def _sleep_with_abort(seconds: float, signal: Optional[AbortSignal]) -> None:
    """Internal sleep implementation with abort polling."""
    if signal is None:
        await asyncio.sleep(seconds)
        return

    # 每 10ms 检查一次中断信号，平衡响应速度和 CPU 占用
    check_interval = 0.5
    remaining = seconds

    while remaining > 0:
        if signal.aborted:
            raise RuntimeError("Aborted")

        step = min(check_interval, remaining)
        await asyncio.sleep(step)
        remaining -= step
