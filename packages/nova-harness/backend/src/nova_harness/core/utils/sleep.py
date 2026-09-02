import asyncio
from typing import Optional

from nova_ai import AbortSignal


async def sleep(seconds: float, signal: Optional[AbortSignal] = None) -> None:
    """
    Sleep helper that respects abort signal.

    Args:
        seconds: Duration to sleep in seconds
        signal: Optional AbortSignal to abort the sleep

    Raises:
        RuntimeError: If the signal is already aborted when sleep starts
        RuntimeError: If the signal is aborted during sleep or the task is cancelled
    """
    if signal is not None and signal.aborted:
        raise RuntimeError("Aborted")

    try:
        if signal is None:
            await asyncio.sleep(seconds)
        else:
            await asyncio.wait_for(signal.wait(), timeout=seconds)
    except asyncio.CancelledError:
        raise RuntimeError("Aborted") from None
    except asyncio.TimeoutError:
        # signal 等待正常超时，未触发中断
        return

    # signal.wait() 在未超时的情况下返回，说明 signal 已被触发
    if signal is not None:
        raise RuntimeError("Aborted")
