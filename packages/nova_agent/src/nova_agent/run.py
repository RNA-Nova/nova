"""单次 Agent run 的登记、事件流、终态、取消与完整收尾。"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Generator, List, Optional

from nova_ai import EventStream
from nova_async import AbortController, AbortedError, AbortSignal, is_aborted
from nova_protocol import (
    AgentEndEvent,
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    MessageEndEvent,
    MessageStartEvent,
    TurnEndEvent,
)

RunEventStream = EventStream[AgentEvent, List[AgentMessage]]
RunEventSink = Callable[[AgentEvent], Awaitable[None]]
RunFailureHandler = Callable[[Exception], Awaitable[None]]
RunSettleHandler = Callable[[], None]
RunStartAbandonmentHandler = Callable[[], None]

_GRACEFUL_ABORT_TIMEOUT_S = 0.1
logger = logging.getLogger(__name__)


def create_run_event_stream() -> RunEventStream:
    """创建单次 run 的权威事件流；agent_end 关闭流并给出全部新消息。"""

    return RunEventStream(
        is_complete=lambda event: event.type == "agent_end",
        extract_result=lambda event: (
            event.messages if isinstance(event, AgentEndEvent) else []
        ),
    )


@dataclass
class AgentRun:
    """一次 run 的运行句柄；事件生产与观察消费相互独立。"""

    abort_controller: AbortController
    events: RunEventStream = field(default_factory=create_run_event_stream)
    task: Optional[asyncio.Task[None]] = None
    watchdog: Optional[asyncio.Task[None]] = None
    turn_end_seen: bool = False
    agent_end_seen: bool = False
    new_messages: List[AgentMessage] = field(default_factory=list)

    @property
    def signal(self) -> AbortSignal:
        return self.abort_controller.signal

    def __await__(self) -> Generator[object, None, None]:
        """等待完整 settle；调用方取消会传播为 run 的硬取消。"""

        async def _wait() -> None:
            if self.task is not None:
                await self.task

        return _wait().__await__()

    def start(
        self,
        executor: Callable[[], Awaitable[None]],
        on_failure: RunFailureHandler,
        on_abandoned_start: RunStartAbandonmentHandler,
        on_settle: RunSettleHandler,
    ) -> asyncio.Task[None]:
        """用同一个 Task 执行、补齐终态并完成全部收尾后落定。"""

        started = False

        async def _run() -> None:
            nonlocal started
            started = True
            try:
                await executor()
            except asyncio.CancelledError:
                # 直接取消 Task 是硬取消；Agent.abort 则已先走 signal 协作取消。
                externally_cancelled = not is_aborted(self.signal)
                await on_failure(AbortedError("Operation aborted"))
                if externally_cancelled:
                    raise
            except Exception as error:
                await on_failure(error)
            finally:
                await self._stop_watchdog()
                on_settle()

        coro = _run()
        task = asyncio.create_task(coro)

        def _on_done(done_task: asyncio.Task[None]) -> None:
            if started:
                return
            # create_task 只负责调度：首步前取消时协程体不会执行，必须在此
            # 同步补齐内部/事件流终态并清理 active run，再唤醒等待者。
            coro.close()
            try:
                on_abandoned_start()
            finally:
                on_settle()

        self.task = task
        task.add_done_callback(_on_done)
        return task

    async def _stop_watchdog(self) -> None:
        watchdog = self.watchdog
        if watchdog is not None:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
            self.watchdog = None

    async def wait_for_idle(self) -> None:
        """等待完整 settle；取消等待者不传染给 run，run 自身取消不算等待者取消。"""

        running_task = self.task
        if running_task is None:
            return
        try:
            await asyncio.shield(running_task)
        except asyncio.CancelledError:
            if not running_task.cancelled():
                raise

    def abort(self) -> None:
        """先协作取消；超过预算后标记硬取消并强制结束 task。"""

        self.abort_controller.abort()
        running_task = self.task
        if (
            running_task is not None
            and not running_task.done()
            and (self.watchdog is None or self.watchdog.done())
        ):
            self.watchdog = asyncio.create_task(
                self._force_abort_after_grace(running_task)
            )

    async def _force_abort_after_grace(self, running_task: asyncio.Task) -> None:
        try:
            async with asyncio.timeout(_GRACEFUL_ABORT_TIMEOUT_S):
                await asyncio.shield(running_task)
        except TimeoutError:
            if not running_task.done():
                running_task.cancel()
                logger.warning(
                    "Agent run did not finish within 100ms of abort; forced cancellation"
                )
        except asyncio.CancelledError:
            # run 已取消；收尾由该 run 自己完成，watchdog 无需再介入。
            return

    def mark_event(self, event: AgentEvent) -> None:
        """登记事件阶段与本次新消息，供失败终态补齐使用。"""

        if isinstance(event, MessageEndEvent):
            self.new_messages.append(event.message)
        elif isinstance(event, TurnEndEvent):
            self.turn_end_seen = True
        elif isinstance(event, AgentEndEvent):
            self.agent_end_seen = True

    def failure_events(self, failure_message: AssistantMessage) -> List[AgentEvent]:
        """按当前阶段生成应补的终态序列，不产生重复 agent_end。"""

        if self.agent_end_seen:
            return []
        if self.turn_end_seen:
            return [AgentEndEvent(messages=list(self.new_messages))]
        return [
            MessageStartEvent(message=failure_message),
            MessageEndEvent(message=failure_message),
            TurnEndEvent(message=failure_message, tool_results=[]),
            AgentEndEvent(messages=[failure_message]),
        ]

    async def handle_failure(
        self, failure_message: AssistantMessage, emit: RunEventSink
    ) -> None:
        """按事件阶段补齐终态；事件流与观察消费互不影响。"""

        for event in self.failure_events(failure_message):
            await emit(event)


__all__ = ["AgentRun", "RunEventStream", "create_run_event_stream"]
