"""
事件流处理
"""

from typing import TypeVar, Generic, AsyncIterator, Optional
import asyncio
from asyncio import Queue

from ..core.messages import AssistantMessage
from .events import AssistantMessageEvent, DoneEvent, ErrorEvent


T = TypeVar('T')
R = TypeVar('R')

TIME_OUT = 60
class EventStream(Generic[T, R], AsyncIterator[T]):
    """
    通用事件流类，支持异步迭代
    """

    def __init__(self, is_complete_func, extract_result_func):
        self._queue: asyncio.Queue[T] = asyncio.Queue()
        self._done = False
        self._final_result_future: asyncio.Future[R] = asyncio.Future()
        self._is_complete = is_complete_func
        self._extract_result = extract_result_func

    def push(self, event: T) -> None:
        """推送事件到流中"""
        if self._done:
            return

        if self._is_complete(event):
            self._done = True
            try:
                result = self._extract_result(event)
                if not self._final_result_future.done():
                    self._final_result_future.set_result(result)
            except Exception as e:
                if not self._final_result_future.done():
                    self._final_result_future.set_exception(e)

        # 将事件放入队列
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(lambda: self._queue.put_nowait(event))
        except RuntimeError:
            # 没有运行中的事件循环，直接放入
            self._queue.put_nowait(event)

    def end(self, result: Optional[R] = None) -> None:
        """结束流"""
        self._done = True
        if result is not None and not self._final_result_future.done():
            self._final_result_future.set_result(result)
        elif not self._final_result_future.done():
            self._final_result_future.set_exception(
                StopAsyncIteration("Stream ended without result")
            )

    async def __anext__(self) -> T:
        """实现 AsyncIterator 的 __anext__ 方法"""
        # 如果队列为空且已完成，则停止迭代
        if self._queue.empty() and self._done:
            raise StopAsyncIteration
        
        # 获取下一个事件
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=TIME_OUT)
                return event
            except asyncio.TimeoutError:
                # 如果超时但流已完成且队列为空，则停止
                if self._queue.empty() and self._done:
                    raise StopAsyncIteration
                continue

    def __aiter__(self) -> AsyncIterator[T]:
        """返回异步迭代器自身"""
        return self

    async def result(self) -> R:
        """获取最终结果"""
        return await self._final_result_future


class AssistantMessageEventStream(EventStream[AssistantMessageEvent, AssistantMessage]):
    """
    助手消息事件流
    """

    def __init__(self):
        def is_complete(event: AssistantMessageEvent) -> bool:
            return event.type in ["done", "error"]

        def extract_result(event: AssistantMessageEvent) -> AssistantMessage:
            if event.type == "done":
                return event.message
            elif event.type == "error":
                return event.error
            raise ValueError(f"Unexpected event type for final result: {event.type}")

        super().__init__(is_complete, extract_result)


def create_assistant_message_event_stream() -> AssistantMessageEventStream:
    """
    创建助手消息事件流的工厂函数（用于扩展）
    """
    return AssistantMessageEventStream()