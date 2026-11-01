"""``gateway/streams.py`` 测试——lazy_stream 取消承诺（结构闭合 + 铁律三回归锁）。"""

import asyncio

import pytest
from nova_protocol import KnownApi, KnownProvider, Model, ModelCost

from nova_ai.gateway.streams import lazy_stream


def _model() -> Model:
    return Model(
        id="m",
        name="m",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.OPENAI,
        base_url="http://x",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=8192,
    )


@pytest.mark.asyncio
async def test_cancel_terminates_without_error_event():
    """lazy_stream 取消承诺三钉（装配中途被取消的场景）：

    1. 驱动任务被取消 → 外壳流消费者收到终结（v2 结构闭合兜底——不悬挂）；
    2. 全程无任何事件入流——取消不伪装成 ErrorEvent 终态（铁律三）；
    3. ``result()`` 落定 ``RuntimeError("ended without result")``——不永久
       悬挂（外壳流 result 通道不携带取消语义，取消语义留在任务层）。
    """

    async def hanging_setup():
        await asyncio.Event().wait()  # 永不就绪——装配中途被取消
        raise AssertionError("unreachable")

    stream = lazy_stream(_model(), hanging_setup)

    waiting = asyncio.Event()
    received = []
    consumed = asyncio.Event()

    async def consume():
        waiting.set()
        async for event in stream:
            received.append(event)
        consumed.set()

    consumer = asyncio.ensure_future(consume())
    await waiting.wait()  # 消费者已就位（事件编排，零完成型 sleep）
    await asyncio.sleep(0)  # 引导：让消费者任务落到 queue.get() 等待点

    task = stream.task
    assert task is not None
    task.cancel()

    await asyncio.wait_for(consumed.wait(), timeout=1.0)  # 钉 1：终结不悬挂
    await consumer

    assert received == []  # 钉 2：零事件——ErrorEvent 未出现
    with pytest.raises(RuntimeError, match="ended without result"):  # 钉 3
        await stream.result()
    assert stream.task is None  # 驱动任务完成自清
