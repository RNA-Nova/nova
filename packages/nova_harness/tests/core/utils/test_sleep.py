"""
Sleep helper 单元测试。
"""

import asyncio

import pytest
from nova_agent import AbortSignal

from nova_harness.core.utils.sleep import sleep


@pytest.mark.asyncio
async def test_sleep_without_signal():
    """无信号时正常完成睡眠。"""
    await sleep(0.01)


@pytest.mark.asyncio
async def test_sleep_with_already_aborted_signal():
    """信号已中断时立即抛出 RuntimeError。"""
    signal = AbortSignal()
    signal.set()
    with pytest.raises(RuntimeError, match="Aborted"):
        await sleep(0.01, signal)


@pytest.mark.asyncio
async def test_sleep_aborted_during_sleep():
    """睡眠期间信号被中断后抛出 RuntimeError。"""
    signal = AbortSignal()

    async def abort_later():
        await asyncio.sleep(0.05)
        signal.set()

    asyncio.create_task(abort_later())
    with pytest.raises(RuntimeError, match="Aborted"):
        await sleep(10, signal)


@pytest.mark.asyncio
async def test_sleep_task_cancelled_translated_to_aborted():
    """外部取消任务时被捕获并转换为 RuntimeError。"""

    async def sleeper():
        return await sleep(10)

    task = asyncio.create_task(sleeper())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(RuntimeError, match="Aborted"):
        await task
