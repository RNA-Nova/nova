"""
Event Stream

设计特点：

- Direct Handoff（优先直接交付等待消费者）
- Buffered（无人消费时缓存）
- AsyncIterator
- Final Result Future
- Error Propagation
- Cancellation Safe
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import (
    AsyncIterator,
    Callable,
    Deque,
    Generic,
    Optional,
    TypeVar,
)

from .types.events import AssistantMessageEvent
from .types.messages import AssistantMessage

T = TypeVar("T")
R = TypeVar("R")

_END_SENTINEL = object()


class EventStream(Generic[T, R], AsyncIterator[T]):
    """
    通用事件流

    生命周期：

        push(event)
            ↓
        async for event in stream

        complete event
            ↓
        await stream.result()

    """

    def __init__(
        self,
        is_complete: Callable[[T], bool],
        extract_result: Callable[[T], R],
    ):
        self._queue: Deque[T] = deque()
        self._waiting: Deque[asyncio.Future] = deque()

        self._done = False

        self._is_complete = is_complete
        self._extract_result = extract_result

        self._result_future: Optional[asyncio.Future[R]] = None
        self._pending_result: Optional[R] = None
        self._pending_exception: Optional[BaseException] = None

    # -------------------------
    # Internal
    # -------------------------

    def _ensure_result_future(self) -> asyncio.Future[R]:
        if self._result_future is None:
            loop = asyncio.get_running_loop()
            self._result_future = loop.create_future()
            if self._pending_exception is not None:
                self._result_future.set_exception(self._pending_exception)
            elif self._pending_result is not None:
                self._result_future.set_result(self._pending_result)
        return self._result_future

    def _set_result(self, result: R) -> None:
        future = self._result_future
        if future is not None and not future.done():
            future.set_result(result)
        else:
            self._pending_result = result

    def _set_exception(self, exc: BaseException) -> None:
        future = self._result_future
        if future is not None and not future.done():
            future.set_exception(exc)
        else:
            self._pending_exception = exc

    def _wake_all(self) -> None:
        while self._waiting:
            waiter = self._waiting.popleft()

            if waiter.done():
                continue

            waiter.set_result(_END_SENTINEL)

    # -------------------------
    # Producer API
    # -------------------------

    def push(self, event: T) -> None:
        """
        推送事件
        """

        if self._done:
            return

        if self._is_complete(event):
            self._done = True

            try:
                result = self._extract_result(event)
                self._set_result(result)

            except Exception as exc:
                self._set_exception(exc)

        # 优先直接交付等待中的消费者
        while self._waiting:
            waiter = self._waiting.popleft()

            if waiter.done():
                continue

            waiter.set_result(event)
            return

        # 无消费者等待则缓冲
        self._queue.append(event)

    def end(
        self,
        result: Optional[R] = None,
        exc: Optional[BaseException] = None,
    ) -> None:
        """
        强制结束流

        可选：
            end(result=...)
            end(exc=...)
        """

        if self._done:
            return

        self._done = True

        if exc is not None:
            self._set_exception(exc)

        elif result is not None:
            self._set_result(result)

        else:
            self._set_exception(RuntimeError("EventStream ended without result"))

        self._wake_all()

    # -------------------------
    # Consumer API
    # -------------------------

    async def __anext__(self) -> T:

        if self._queue:
            return self._queue.popleft()

        if self._done:
            raise StopAsyncIteration

        loop = asyncio.get_running_loop()

        waiter = loop.create_future()
        self._waiting.append(waiter)

        try:
            item = await waiter

            if item is _END_SENTINEL:
                raise StopAsyncIteration

            return item

        finally:
            try:
                self._waiting.remove(waiter)
            except ValueError:
                pass

    def __aiter__(self) -> AsyncIterator[T]:
        return self

    async def result(self) -> R:
        """
        获取最终结果

        Returns:
            R

        Raises:
            Exception
        """
        future = self._ensure_result_future()
        return await future


class AssistantMessageEventStream(
    EventStream[
        AssistantMessageEvent,
        AssistantMessage,
    ]
):
    def __init__(self):

        def is_complete(
            event: AssistantMessageEvent,
        ) -> bool:
            return event.type in ("done", "error")

        def extract_result(
            event: AssistantMessageEvent,
        ) -> AssistantMessage:

            if event.type == "done":
                return event.message

            if event.type == "error":
                return event.error

            raise ValueError(f"Unexpected event type: {event.type}")

        super().__init__(
            is_complete,
            extract_result,
        )


def create_assistant_message_event_stream() -> AssistantMessageEventStream:
    """
    创建助手消息事件流的工厂函数（用于扩展）
    """
    return AssistantMessageEventStream()
