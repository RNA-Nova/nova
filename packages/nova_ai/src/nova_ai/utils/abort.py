"""Abort 竞速与信号基建（对齐 TS ``src/utils/abort.ts``）。

gateway / auth / 协议实现的公共底座：把"可中断地等待一个操作"做成原语，
让 abort 语义能贯穿鉴权解析、模型刷新等流式之外的入口。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Iterable, Optional, TypeVar

from ..signal import AbortController, AbortedError, AbortSignal

T = TypeVar("T")

__all__ = ["any_signal", "operation_signal", "race_with_abort"]


def operation_signal(signal: Optional[AbortSignal]) -> AbortSignal:
    """为 signal 可选的公开入口补一个永不上报中断的操作内信号（对齐 TS operationSignal）。"""
    return signal if signal is not None else AbortController(name="operation").signal


def any_signal(signals: Iterable[Optional[AbortSignal]]) -> AbortSignal:
    """任一输入中断即中断；全空时返回永不中断的操作内信号。"""
    combined = AbortSignal.any(signals)
    return combined if combined is not None else operation_signal(None)


def _consume(awaitable: Awaitable[T]) -> Awaitable[T]:
    """把裸协程包成 Task（竞速后仍需被观察，避免 unretrieved-exception 警告）。"""

    async def _run() -> T:
        try:
            return await awaitable
        except asyncio.CancelledError:
            raise
        except Exception:
            return None  # type: ignore[return-value]

    return asyncio.ensure_future(_run())


async def race_with_abort(operation: Awaitable[T], signal: Optional[AbortSignal]) -> T:
    """等待 ``operation`` 完成；期间 ``signal`` 中断则立即抛 :class:`AbortedError`。

    对齐 TS ``raceWithAbortSignal``：被抛弃的操作不会无人观察——它转为后台
    Task 继续运行，其结果/异常被静默吸收（调用方已选择放弃它）。调用方
    想真正取消底层工作，应自己持有取消句柄（如 refresh 的 per-provider
    controller）而不是依赖本函数。
    """
    sig = signal
    if sig is not None and sig.aborted:
        asyncio.ensure_future(_consume(operation))
        raise AbortedError("The operation was aborted")

    task = asyncio.ensure_future(operation)
    if sig is None:
        return await task

    waiter = asyncio.ensure_future(sig.wait())
    try:
        done, _pending = await asyncio.wait(
            {task, waiter}, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            waiter.cancel()
            return task.result()
        # signal 先到：被抛弃的 task 转后台观察
        task.add_done_callback(
            lambda _t: _t.exception() if not _t.cancelled() else None
        )
        raise AbortedError("The operation was aborted")
    finally:
        if not waiter.done():
            waiter.cancel()
