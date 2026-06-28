"""Agent 事件分发与持久化控制。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nova_agent import AgentMessage

from nova_harness.core.types.events import (
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
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from nova_harness.core.utils.messages import extract_text_from_content

if TYPE_CHECKING:
    from nova_harness.core.agent_session.agent import AgentSession


class EventController:
    """封装 AgentSession 的底层 Agent 事件处理、扩展转发与消息持久化。"""

    def __init__(self, session: "AgentSession") -> None:
        self._session = session

    async def handle(self, event: Any) -> None:
        """处理底层 Agent 事件：持久化消息、转发扩展、维护内部状态。"""
        event_type = getattr(event, "type", None)

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

        # 先转发给扩展 runner
        if self._session._extension_runner is not None:
            await self.forward_to_runner(event)

        # 再通知外部监听者（agent_end 补充 willRetry）
        if event_type == AGENT_END:
            self._session._emit(
                AgentEndEvent(
                    messages=getattr(event, "messages", []),
                    will_retry=self._session._retry.will_retry_after_agent_end(event),
                )
            )
        else:
            self._session._emit(event)

        # 持久化与内部状态维护
        if event_type == MESSAGE_END:
            message = getattr(event, "message", None)
            if message is not None:
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
                TurnStartEvent(turn_index=getattr(event, "turn_index", 0))
            )
        elif event_type == TURN_END:
            await runner.emit(
                TurnEndEvent(
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
            replacement = await runner.emit_message_end(original_message)
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
