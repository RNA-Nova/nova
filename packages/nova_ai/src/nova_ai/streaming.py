"""
Event Stream（v2：``asyncio.Queue`` 底座）

设计特点：

- Direct Handoff（队列原生：等待中的消费者即时被唤醒）
- Buffered（无人消费时缓存）
- AsyncIterator
- Final Result Future
- Error Propagation
- Cancellation Safe
- **结构闭合**：``drive()`` 的驱动任务以任何方式退出（正常/异常/外部
  掀桌）都终结本流——"驱动暴毙 → 流不终结"的潜伏挂死通道结构性消灭。

底座：`asyncio.Queue`（3.13+ 的 ``shutdown()`` 提供通道关闭语义——
消费者先排干缓冲再收 ``QueueShutDown``，即"终态不重不漏"的平台形态；
``maxsize`` 为背压旋钮）。手写并发容器（deque + Future 等待队列）退役。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Callable, Coroutine, Generic, Optional, TypeVar

from nova_protocol import AssistantMessage, AssistantMessageEvent

T = TypeVar("T")
R = TypeVar("R")


class EventStream(Generic[T, R], AsyncIterator[T]):
    """
    通用事件流

    **单消费者契约**：一条流只允许一个协程做 ``async for`` 迭代消费
    （事件按递件语义交付一次，不是广播）。只要终值用 ``result()``
    （多方 await 安全）；要多方订阅在上层扇出（listener 列表 / 事件
    总线），不要再起迭代者。第二个消费者与既有等待者**重叠等待**时
    ``__anext__`` 直接抛 ``RuntimeError``（fail-fast——静默挂死的
    排错成本远高于当场报错；串行复用不受限）。

    生命周期：

        push(event)
            ↓
        async for event in stream

        complete event
            ↓
        await stream.result()

    ``maxsize``：背压旋钮（默认 0 = 无界，与 v1 行为逐字节一致）。
    有界时 ``push`` 溢出抛 ``asyncio.QueueFull``——为预留语义，
    启用背压前先定义溢出策略（阻塞/丢弃）。
    """

    def __init__(
        self,
        is_complete: Callable[[T], bool],
        extract_result: Callable[[T], R],
        *,
        maxsize: int = 0,
    ):
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        self._done = False

        self._is_complete = is_complete
        self._extract_result = extract_result

        self._result_future: Optional[asyncio.Future[R]] = None
        self._pending_result: Optional[R] = None
        self._pending_exception: Optional[BaseException] = None

        # 驱动任务槽位：流自持驱动自己的后台任务（drive() 写入，完成自清）。
        # 事件循环对在途任务只持弱引用，无强引用的后台任务可能被 GC 中途
        # 回收（流式静默冻死）——流的寿命即驱动任务的寿命，故由流持有。
        self._driver_task: Optional[asyncio.Task[None]] = None

        # 单消费者契约哨兵（重叠等待 fail-fast）
        self._consuming = False

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
        elif self._pending_result is None and self._pending_exception is None:
            # 先到者为准（docstring 契约）——已有暂存终值/异常时不覆盖
            self._pending_result = result

    def _set_exception(self, exc: BaseException) -> None:
        future = self._result_future
        if future is not None and not future.done():
            future.set_exception(exc)
        elif self._pending_result is None and self._pending_exception is None:
            self._pending_exception = exc

    # -------------------------
    # Producer API
    # -------------------------

    @property
    def task(self) -> Optional[asyncio.Task[None]]:
        """驱动本流的后台任务（完成即自清；只读）。"""
        return self._driver_task

    @property
    def done(self) -> bool:
        """流是否已终结（终态事件已发布）。迟到异常不得改写已发布消息
        （Done→abort 竞态防护见两 ``_stream.py`` 的 except 块）。"""
        return self._done

    def drive(self, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """以本流为宿主驱动后台任务。

        流持有任务的强引用直到其完成——调用方拿到流即拿到任务的命，
        裸 ``asyncio.create_task`` 的返回值无人持有会被 GC 中途回收
        （官方文档警告）；流的寿命与驱动任务的寿命天然重合，是最天然的
        持有者。1:N 的属主台账场景用 ``nova_async.TaskTracker``。

        **结构闭合**：驱动任务以任何方式退出都终结本流——正常退出
        ``end()`` 幂等；异常 ``end(exc=...)``（错误进 result 通道，
        ``result()`` 的 await 方拿到真错而非悬挂）；外部掀桌（如关停
        路径 ``TaskTracker.cancel_all``）``end()`` 终结不悬挂。兜底
        落在 done 回调层而非协程体内——"创建后首步前取消"时协程体
        一行不执行（try/finally 均不跑），体内的收尾靠不住（实测）。
        """
        if self._done:
            coro.close()  # 不起跑即关：防 "coroutine was never awaited" 警告
            raise RuntimeError("Cannot drive a task on a completed stream")

        started = False

        async def _driven() -> None:
            nonlocal started
            started = True
            try:
                await coro
            except asyncio.CancelledError:
                self.end()  # 外部掀桌：终结但不污染 result 的异常语义
                raise
            except Exception as error:
                self.end(exc=error)  # 驱动暴毙：错误进 result 通道
            finally:
                self.end()  # 幂等双保险（非常规退出也要终结流）

        def _on_done(done_task: asyncio.Task[None]) -> None:
            if self._driver_task is done_task:
                self._driver_task = None
            if not started:
                # 首步前取消：协程体一行未跑——终结兜底只能在回调层
                # （done_callback 对未起跑任务也执行）；内层协程从未起跑，
                # 主动关防 "coroutine was never awaited" 告警
                coro.close()
                self.end()
            elif not self._done:
                self.end()  # 双保险（正常路径体内已 end，幂等）

        task = asyncio.get_running_loop().create_task(_driven())
        self._driver_task = task
        task.add_done_callback(_on_done)
        return task

    def push(self, event: T) -> None:
        """
        推送事件（终态事件入队即关通道：消费者排干缓冲后收 QueueShutDown）。
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

            self._queue.put_nowait(event)
            # 终态即关（排干语义）：缓冲事件全部送达后消费者才终结
            self._queue.shutdown()
            return

        self._queue.put_nowait(event)

    def end(
        self,
        result: Optional[R] = None,
        exc: Optional[BaseException] = None,
    ) -> None:
        """
        强制结束流（幂等——重复调用安全，结果以先到者为准）。

        关通道（``Queue.shutdown``）——等待中的消费者排干缓冲后终结
        （排干即"无条件唤醒等待者"的 v2 对位：缓冲事件不重不漏）。

        可选：
            end(result=...)
            end(exc=...)
        """

        already_done = self._done
        self._done = True

        if exc is not None:
            self._set_exception(exc)

        elif result is not None:
            self._set_result(result)

        elif not already_done:
            self._set_exception(RuntimeError("EventStream ended without result"))

        self._queue.shutdown()

    # -------------------------
    # Consumer API
    # -------------------------

    async def __anext__(self) -> T:

        if self._consuming:
            # 单消费者契约（见类 docstring）：已有等待者在场说明第二个
            # 消费者进场重叠等待——fail-fast，不让它静默挂死
            raise RuntimeError(
                "EventStream is single-consumer: another consumer is already "
                "waiting. Use result() for the final value, or fan out at a "
                "higher layer."
            )

        self._consuming = True
        try:
            return await self._queue.get()
        except asyncio.QueueShutDown:
            # 通道关闭且缓冲已排干 → 迭代终结
            raise StopAsyncIteration from None
        finally:
            self._consuming = False

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
        # 等待者取消只取消自己的等待，不得取消共享终值 Future。
        return await asyncio.shield(future)


class AssistantMessageEventStream(
    EventStream[
        AssistantMessageEvent,
        AssistantMessage,
    ]
):
    def __init__(self):
        self._started_at = int(time.time() * 1000)
        self._started_at_monotonic = time.monotonic()

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

    def _time_response(self, message: AssistantMessage) -> None:
        """只给本流见证其开始的响应盖耗时；旧消息/延迟结果保持缺席。"""
        if (
            self.done
            or message.duration_ms is not None
            or message.timestamp < self._started_at
        ):
            return
        message.duration_ms = max(
            0, round((time.monotonic() - self._started_at_monotonic) * 1000)
        )

    def push(self, event: AssistantMessageEvent) -> None:
        if event.type == "done":
            self._time_response(event.message)
        elif event.type == "error":
            self._time_response(event.error)
        super().push(event)

    def end(
        self,
        result: Optional[AssistantMessage] = None,
        exc: Optional[BaseException] = None,
    ) -> None:
        if result is not None:
            self._time_response(result)
        super().end(result=result, exc=exc)


def create_assistant_message_event_stream() -> AssistantMessageEventStream:
    """
    创建助手消息事件流的工厂函数（用于扩展）
    """
    return AssistantMessageEventStream()
