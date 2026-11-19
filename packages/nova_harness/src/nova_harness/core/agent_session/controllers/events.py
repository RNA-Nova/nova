"""Agent 事件消费、持久化与扩展工作队列。"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from nova_protocol import AgentMessage, AssistantMessage

from nova_harness.core.agent_session.protocol import AgentSessionProtocol
from nova_harness.core.utils.messages import extract_text_from_content
from nova_harness.events import (
    AGENT_END,
    AGENT_START,
    MESSAGE_END,
    MESSAGE_START,
    MESSAGE_UPDATE,
    TOOL_EXECUTION_END,
    TOOL_EXECUTION_START,
    TOOL_EXECUTION_UPDATE,
    TURN_END,
    TURN_START,
    AgentEndEvent,
    AgentStartEvent,
    AutoRetryEndEvent,
    CacheMissEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)

logger = logging.getLogger(__name__)


@dataclass
class _RunnerWork:
    event: Any
    completion: Optional[asyncio.Future[None]] = None


class EventController:
    """系统事件处理与第三方扩展的独立、有序工作队列。"""

    def __init__(self, session: AgentSessionProtocol) -> None:
        self._session = session
        self._turn_index = 0
        self._runner_queue: asyncio.Queue[_RunnerWork] = asyncio.Queue()
        self._runner_task: Optional[asyncio.Task[None]] = None

    def _ensure_runner_worker(self) -> None:
        if self._session._extension_runner is None or self._runner_task is not None:
            return
        self._runner_task = self._session._tasks.spawn(
            self._drain_runner(), name="extension-events"
        )

    def emit_to_runner(self, event: Any) -> None:
        """观察型事件只入队，不阻塞系统消费和 Agent 生产。"""
        if self._session._extension_runner is None:
            return
        self._ensure_runner_worker()
        self._runner_queue.put_nowait(_RunnerWork(event=event))

    async def on_message_end(self, message: AgentMessage, signal: Any = None) -> None:
        """干预型消息改写：保持顺序并等待完成，但不进入观察订阅。"""
        if self._session._extension_runner is None:
            return
        self._ensure_runner_worker()
        completion = asyncio.get_running_loop().create_future()
        self._runner_queue.put_nowait(
            _RunnerWork(
                event=MessageEndEvent(message=message),
                completion=completion,
            )
        )
        await completion

    async def _drain_runner(self) -> None:
        while True:
            work = await self._runner_queue.get()
            completion = work.completion
            if completion is not None and completion.cancelled():
                continue
            try:
                await self.forward_to_runner(work.event)
            except Exception as error:
                if completion is not None and not completion.done():
                    completion.set_exception(error)
                else:
                    logger.error(
                        "Extension event dispatch failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )
            else:
                if completion is not None and not completion.done():
                    completion.set_result(None)

    async def handle(self, event: Any, signal: Any = None) -> None:
        """处理系统事件；第三方观察分发独立进行。"""
        event_type = getattr(event, "type", None)

        if event_type == AGENT_START:
            self._turn_index = 0
            self._session._current_run_id = uuid.uuid4().hex[:8]

        # 当用户消息开始消费时，先从待处理队列中移除并更新 UI
        if (
            event_type == MESSAGE_START
            and getattr(getattr(event, "message", None), "role", None) == "user"
        ):
            self._session._overflow_recovery_attempted = False
            text = extract_text_from_content(getattr(event.message, "content", ""))
            if text:
                if text in self._session._steering_messages:
                    self._session._steering_messages.remove(text)
                    self._session._queue.emit_update()
                elif text in self._session._follow_up_messages:
                    self._session._follow_up_messages.remove(text)
                    self._session._queue.emit_update()

        # message_end 的改写已经经 on_message_end 完成，其余观察事件只入队。
        if event_type != MESSAGE_END:
            self.emit_to_runner(event)

        if event_type == AGENT_START:
            self._session._emit(
                AgentStartEvent(run_id=self._session._current_run_id or "")
            )
        elif event_type == AGENT_END:
            self._session._emit(
                AgentEndEvent(
                    messages=getattr(event, "messages", []),
                    will_retry=self._session._retry.will_retry_after_agent_end(event),
                    run_id=self._session._current_run_id or "",
                )
            )
            self._session._current_run_id = None
        else:
            self._session._emit(event)

        # 持久化与内部状态维护
        if event_type == MESSAGE_END:
            message = getattr(event, "message", None)
            if message is not None:
                if getattr(message, "role", None) == "assistant" and getattr(
                    message, "stop_reason", None
                ) not in ("aborted", "error"):
                    self._maybe_emit_cache_miss(message)
                await self.persist_message(message)
                if getattr(message, "role", None) == "assistant":
                    self._session._last_assistant_message = message
                    assistant_msg = message
                    if getattr(assistant_msg, "stop_reason", None) != "error":
                        self._session._overflow_recovery_attempted = False
                        if self._session._retry_attempt > 0:
                            self._session._emit(
                                AutoRetryEndEvent(
                                    success=True, attempt=self._session._retry_attempt
                                )
                            )
                            self._session._retry_attempt = 0

        if event_type == TURN_END:
            self._turn_index += 1

    def close(self) -> None:
        """停止扩展工作队列；会话任务台账仍负责统一排水。"""
        if self._runner_task is not None:
            self._runner_task.cancel()
            self._runner_task = None

    async def aclose(self) -> None:
        """关闭并等待扩展工作队列落定，供测试和显式宿主收尾使用。"""
        task = self._runner_task
        self.close()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def forward_to_runner(self, event: Any) -> None:
        """把底层 Agent 事件映射为 Nova 扩展事件并分发给 runner。"""
        runner = self._session._extension_runner
        if runner is None:
            return

        event_type = getattr(event, "type", None)
        if event_type == AGENT_START:
            await runner.emit(AgentStartEvent())
        elif event_type == AGENT_END:
            await runner.emit(AgentEndEvent(messages=getattr(event, "messages", [])))
        elif event_type == TURN_START:
            await runner.emit(
                TurnStartEvent(
                    turn_index=self._turn_index,
                    timestamp=int(time.time() * 1000),
                )
            )
        elif event_type == TURN_END:
            await runner.emit(
                TurnEndEvent(
                    turn_index=self._turn_index,
                    message=getattr(event, "message", None),
                    tool_results=getattr(event, "tool_results", []),
                )
            )
        elif event_type == MESSAGE_START:
            await runner.emit(
                MessageStartEvent(message=getattr(event, "message", None))
            )
        elif event_type == MESSAGE_UPDATE:
            await runner.emit(
                MessageUpdateEvent(
                    message=getattr(event, "message", None),
                    assistant_message_event=getattr(
                        event, "assistant_message_event", None
                    ),
                )
            )
        elif event_type == MESSAGE_END:
            original_message = getattr(event, "message", None)
            replacement = await runner.emit_message_end(
                MessageEndEvent(message=original_message)
            )
            if (
                replacement is not None
                and original_message is not None
                and replacement is not original_message
                and getattr(replacement, "role", None) is not None
                and getattr(original_message, "role", None) == replacement.role
            ):
                self.replace_message_in_place(original_message, replacement)
        elif event_type == TOOL_EXECUTION_START:
            await runner.emit(
                ToolExecutionStartEvent(
                    tool_call_id=getattr(event, "tool_call_id", ""),
                    tool_name=getattr(event, "tool_name", ""),
                    args=getattr(event, "args", None),
                )
            )
        elif event_type == TOOL_EXECUTION_UPDATE:
            await runner.emit(
                ToolExecutionUpdateEvent(
                    tool_call_id=getattr(event, "tool_call_id", ""),
                    tool_name=getattr(event, "tool_name", ""),
                    args=getattr(event, "args", None),
                    partial_result=getattr(event, "partial_result", None),
                )
            )
        elif event_type == TOOL_EXECUTION_END:
            await runner.emit(
                ToolExecutionEndEvent(
                    tool_call_id=getattr(event, "tool_call_id", ""),
                    tool_name=getattr(event, "tool_name", ""),
                    result=getattr(event, "result", None),
                    is_error=getattr(event, "is_error", False),
                )
            )
        else:
            # agent_settled 与包级开放事件直接透传，不做形状映射。
            await runner.emit(event)

    def _maybe_emit_cache_miss(self, message: AgentMessage) -> None:
        """message_end 时的 cache-miss 检测与事件发射（settings 门控）。"""
        if not isinstance(message, AssistantMessage):
            return
        session = self._session
        settings_manager = getattr(session, "settings_manager", None)
        if settings_manager is None:
            return
        settings = settings_manager.get_settings()
        if not getattr(settings, "show_cache_miss_notices", False):
            return
        try:
            from nova_harness.sessions.cache_stats import detect_cache_miss

            miss = detect_cache_miss(
                session.session_manager.get_entries(),
                message,
                session.model_runtime,
            )
        except Exception:
            return
        if miss is None:
            return
        session._emit(
            CacheMissEvent(
                missed_tokens=miss.missed_tokens,
                missed_cost=miss.missed_cost,
                idle_ms=miss.idle_ms,
                model_changed=miss.model_changed,
            )
        )

    def replace_message_in_place(
        self, target: AgentMessage, replacement: AgentMessage
    ) -> None:
        """原地替换消息对象，保持 agent state 与持久化引用一致。"""
        if target is replacement:
            return
        target_dict = vars(target) if hasattr(target, "__dict__") else target
        if isinstance(target_dict, dict):
            for key in list(target_dict.keys()):
                del target_dict[key]
            target_dict.update(
                vars(replacement) if hasattr(replacement, "__dict__") else replacement
            )

    async def persist_message(self, message: AgentMessage) -> None:
        """把消息持久化到 SessionManager。"""
        role = getattr(message, "role", None)
        if role == "custom":
            self._session.session_manager.append_custom_message_entry(
                getattr(message, "custom_type", ""),
                getattr(message, "content", ""),
                getattr(message, "display", True),
                getattr(message, "details", None),
            )
        elif role in ("user", "assistant", "toolResult"):
            self._session.session_manager.append_message(message)
