"""自动重试控制。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from nova_ai import AssistantMessage

from nova_harness.core.types.events import AutoRetryEndEvent, AutoRetryStartEvent
from nova_harness.core.utils import is_context_overflow

if TYPE_CHECKING:
    from nova_harness.core.agent_session.agent import AgentSession


class RetryController:
    """封装 AgentSession 的自动错误重试逻辑。"""

    def __init__(self, session: "AgentSession") -> None:
        self._session = session

    @property
    def is_retrying(self) -> bool:
        return self._session._retry_abort_event is not None

    @property
    def attempt(self) -> int:
        return self._session._retry_attempt

    def will_retry_after_agent_end(self, event: Any) -> bool:
        """根据 agent_end 事件判断后续是否会自动重试。"""
        settings = self._session.settings_manager.get_retry_settings()
        if not getattr(
            settings, "enabled", False
        ) or self._session._retry_attempt >= getattr(settings, "max_retries", 0):
            return False
        messages = getattr(event, "messages", [])
        for msg in reversed(messages):
            if getattr(msg, "role", None) == "assistant":
                return self.is_retryable_error(msg)
        return False

    def is_retryable_error(self, message: AssistantMessage) -> bool:
        """判断 assistant 错误消息是否可重试（与 TS 版 `_isRetryableError` 对齐）。"""
        if message.stop_reason != "error" or not message.error_message:
            return False

        # 上下文溢出应由压缩处理，不应进入自动重试
        context_window = getattr(self._session.model, "context_window", None)
        if is_context_overflow(message, context_window):
            return False

        err = message.error_message.lower()
        non_retryable = [
            "usage limit",
            "insufficient_quota",
            "out of budget",
            "quota exceeded",
            "billing",
            "available balance",
            "out of budget",
        ]
        if any(k in err for k in non_retryable):
            return False
        retryable = [
            "overloaded",
            "provider returned error",
            "rate limit",
            "too many requests",
            "429",
            "500",
            "502",
            "503",
            "504",
            "service unavailable",
            "server error",
            "internal error",
            "network error",
            "connection error",
            "connection refused",
            "connection lost",
            "websocket closed",
            "websocket error",
            "other side closed",
            "fetch failed",
            "upstream connect",
            "reset before headers",
            "socket hang up",
            "ended without",
            "stream ended before message_stop",
            "http2 request did not get a response",
            "timed out",
            "timeout",
            "terminated",
            "retry delay",
        ]
        return any(k in err for k in retryable)

    async def prepare_retry(self, message: AssistantMessage) -> bool:
        """为可重试错误准备指数退避续话。"""
        retry_settings = self._session.settings_manager.get_retry_settings()
        if not getattr(retry_settings, "enabled", False):
            return False

        self._session._retry_attempt += 1
        max_retries = getattr(retry_settings, "max_retries", 0) or 0
        if self._session._retry_attempt > max_retries:
            self._session._retry_attempt -= 1
            return False

        base_delay_ms = getattr(retry_settings, "base_delay_ms", 1000) or 1000
        max_delay_ms = getattr(retry_settings, "max_delay_ms", 60000) or 60000
        delay_ms = min(
            base_delay_ms * (2 ** (self._session._retry_attempt - 1)), max_delay_ms
        )
        delay_s = delay_ms / 1000.0

        self._session._emit(
            AutoRetryStartEvent(
                attempt=self._session._retry_attempt,
                max_attempts=max_retries,
                delay_ms=delay_ms,
                error_message=message.error_message or "Unknown error",
            )
        )

        # 移除 agent state 中的错误消息（会话历史已保留）
        messages = list(self._session.agent.state.messages)
        if messages and getattr(messages[-1], "role", None) == "assistant":
            self._session.agent.state.messages = messages[:-1]

        # 可中断的指数退避等待
        self._session._retry_abort_event = asyncio.Event()
        try:
            await asyncio.wait_for(
                self._session._retry_abort_event.wait(), timeout=delay_s
            )
            # 被中断
            attempt = self._session._retry_attempt
            self._session._retry_attempt = 0
            self._session._emit(
                AutoRetryEndEvent(
                    success=False, attempt=attempt, final_error="Retry cancelled"
                )
            )
            return False
        except asyncio.TimeoutError:
            pass
        finally:
            self._session._retry_abort_event = None

        return True

    def abort_retry(self) -> None:
        """取消进行中的自动重试。"""
        if self._session._retry_abort_event is not None:
            self._session._retry_abort_event.set()

    def set_auto_retry_enabled(self, enabled: bool) -> None:
        """开关自动重试设置。"""
        self._session.settings_manager.set_retry_enabled(enabled)
