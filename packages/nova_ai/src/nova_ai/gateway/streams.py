"""ProviderStreams 契约原语（对齐 TS ``src/api/lazy.ts`` 的 ``lazyStream``）。

契约三合一——这是本库的"宪法"：

1. **同步签名**：``stream()`` 立刻返回事件流，调用方拿到即 ``async for``；
2. **异步装配**：auth 解析、provider 查找等必然异步的装配在后台进行，
   事件被搬运进外层流；
3. **错误即流终态**：装配阶段的任何异常（未知 provider、auth 失败……）
   一律编码为 ``ErrorEvent`` 终止流——调用方永远不需要 try/except。

凡是产出 ``ProviderStreams`` 契约流的路径，只允许经由本原语；
不得在 ``lazy_stream`` 之外同步抛错（见 ``ModelsManager.stream`` 的 setup 闭包纪律）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

from nova_protocol import (
    AbortSignal,
    AssistantMessage,
    ErrorEvent,
    Model,
    StopReason,
    Usage,
    is_aborted,
)

from ..streaming import AssistantMessageEventStream
from ..utils.errors import normalize_error


def create_setup_error_message(
    model: Model,
    error: BaseException,
    *,
    stop_reason: StopReason = StopReason.ERROR,
) -> AssistantMessage:
    """构造装配阶段失败的终态消息（对齐 TS createSetupErrorMessage）。

    异常经归一单点分类（``error_kind``/``retry_after_ms`` 上消息，下游按
    kind 分派）。``stop_reason`` 缺省 ERROR；装配期信号已 abort 时调用方
    传 ABORTED（对齐 TS f3a64af07——中断不伪装成错误）。
    """
    normalized = normalize_error(error, domain="setup")
    return AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        stop_reason=stop_reason,
        error_message=normalized.message,
        error_kind=normalized.kind,
        retry_after_ms=(
            int(normalized.retry_after_ms)
            if normalized.retry_after_ms is not None
            else None
        ),
        timestamp=int(time.time() * 1000),
    )


def lazy_stream(
    model: Model,
    setup: Callable[[], Awaitable[AssistantMessageEventStream]],
    signal: Optional[AbortSignal] = None,
) -> AssistantMessageEventStream:
    """同步返回流，``setup`` 在后台异步执行；失败以 error 事件结束（对齐 TS lazyStream）。

    ``signal`` 为请求级取消信号（对齐 TS f3a64af07）：装配失败时信号
    已 abort → 终态报 ``aborted``（用户中断），否则报 ``error``。
    """
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
            aborted = is_aborted(signal)
            message = create_setup_error_message(
                model,
                exc,
                stop_reason=StopReason.ABORTED if aborted else StopReason.ERROR,
            )
            outer.push(
                ErrorEvent(
                    type="error",
                    reason="aborted" if aborted else "error",
                    error=message,
                )
            )
            outer.end(result=message)

    # 驱动任务归流自持（drive 的寿命语义：流的寿命即任务的寿命）——
    # 不再需要进程级在途登记表。
    outer.drive(_run())
    return outer


__all__ = ["create_setup_error_message", "lazy_stream"]
