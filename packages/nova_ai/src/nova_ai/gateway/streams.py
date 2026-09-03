"""ProviderStreams 契约原语（对齐 TS ``src/api/lazy.ts`` 的 ``lazyStream``）。

契约三合一——这是本库的"宪法"：

1. **同步签名**：``stream()`` 立刻返回事件流，调用方拿到即 ``async for``；
2. **异步装配**：auth 解析、provider 查找等必然异步的装配在后台进行，
   事件被搬运进外层流；
3. **错误即流终态**：装配阶段的任何异常（未知 provider、auth 失败……）
   一律编码为 ``ErrorEvent`` 终止流——调用方永远不需要 try/except。

凡是产出 ``ProviderStreams`` 契约流的路径，只允许经由本原语；
不得在 ``lazy_stream`` 之外同步抛错（见 ``Models.stream`` 的 setup 闭包纪律）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Set

from ..streaming import AssistantMessageEventStream
from ..types.enums import StopReason
from ..types.events import ErrorEvent
from ..types.messages import AssistantMessage
from ..types.model import Model, Usage


_INFLIGHT_LAZY_TASKS: Set["asyncio.Task[None]"] = set()
"""在途 lazy_stream 后台任务的强引用集（防 GC 中途回收，完成即弃）。"""


def create_setup_error_message(model: Model, error: Exception) -> AssistantMessage:
    """构造装配阶段失败的终态消息（对齐 TS createSetupErrorMessage）。"""
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        stop_reason=StopReason.ERROR,
        error_message=str(error),
        timestamp=int(time.time() * 1000),
    )


def lazy_stream(
    model: Model,
    setup: Callable[[], Awaitable[Any]],
) -> AssistantMessageEventStream:
    """同步返回流，``setup`` 在后台异步执行；失败以 error 事件结束（对齐 TS lazyStream）。"""
    outer = AssistantMessageEventStream()

    async def _run() -> None:
        try:
            inner = await setup()
            async for event in inner:
                outer.push(event)
            result = await inner.result()
            outer.end(result=result)
        except asyncio.CancelledError:
            # 取消语义归调用方：不把取消伪装成 ErrorEvent 终态
            raise
        except BaseException as exc:
            message = create_setup_error_message(model, exc)
            outer.push(
                ErrorEvent(
                    type="error",
                    reason="error",
                    error=message,
                )
            )
            outer.end(result=message)

    # 持有任务强引用直到完成——事件循环对在途任务只有弱引用，
    # 装配耗时挂起时任务可能被 CPython GC 中途回收（CPython 文档警告）
    task = asyncio.get_running_loop().create_task(_run())
    _INFLIGHT_LAZY_TASKS.add(task)
    task.add_done_callback(_INFLIGHT_LAZY_TASKS.discard)
    return outer


__all__ = ["create_setup_error_message", "lazy_stream"]
