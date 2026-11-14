"""自动重试控制。"""

from __future__ import annotations

from typing import Any

from nova_async import AbortController, AbortedError, abortable_sleep, backoff_delay_ms
from nova_protocol import RETRYABLE_ERROR_KINDS, AssistantMessage, NovaErrorKind

from nova_harness.core.agent_session.protocol import AgentSessionProtocol
from nova_harness.core.utils import is_context_overflow
from nova_harness.events import AutoRetryEndEvent, AutoRetryStartEvent


class RetryController:
    """封装 AgentSession 的自动错误重试逻辑。"""

    def __init__(self, session: AgentSessionProtocol) -> None:
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
        """判断 assistant 错误消息是否可重试——读归一分类（``error_kind``），
        不对文案做子串匹配（铁律二：分类一次、边界完成，下游只读 kind）。
        """
        if message.stop_reason != "error":
            return False

        # 溢出归压缩处理，不进重试：provider 报错经归一的 kind，或用量超窗
        # （错误响应可能未带 usage，两个判据并存）
        if message.error_kind == NovaErrorKind.CONTEXT_OVERFLOW:
            return False
        context_window = getattr(self._session.model, "context_window", None)
        if is_context_overflow(message, context_window):
            return False

        # 未分类（旧数据/未归一）不重试——宁可漏试，不对 INTERNAL 瞎试
        kind = message.error_kind
        return kind is not None and kind in RETRYABLE_ERROR_KINDS

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
        provider = getattr(retry_settings, "provider", None)
        max_delay_ms = 60000
        if provider is not None:
            max_delay_ms = getattr(provider, "max_retry_delay_ms", 60000) or 60000
        # 服务端退避指引（429 Retry-After 族，经归一上消息）优先于本地指数
        # 退避（codex ``retry_delay().unwrap_or(backoff)`` 对位）；封顶 provider 上限
        server_delay_ms = message.retry_after_ms
        if server_delay_ms is not None and server_delay_ms > 0:
            delay_ms = min(server_delay_ms, max_delay_ms)
        else:
            # 退避机制件（nova_async backoff）：_retry_attempt 已 1-indexed；
            # jitter=0 保持现行为（旧公式本无抖动）
            delay_ms = backoff_delay_ms(
                self._session._retry_attempt,
                base_ms=base_delay_ms,
                cap_ms=max_delay_ms,
                jitter=0,
            )

        delay_ms = int(delay_ms)

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

        # 可中断的指数退避等待（可中断等待单点归 nova_async abortable_sleep：
        # AbortedError = 被取消分支；睡满正常返回 = 续话——替代旧 wait_for
        # 反相睡眠）
        self._session._retry_abort_event = AbortController("retry")
        try:
            await abortable_sleep(delay_ms, self._session._retry_abort_event.signal)
        except AbortedError:
            # 被中断
            attempt = self._session._retry_attempt
            self._session._retry_attempt = 0
            self._session._emit(
                AutoRetryEndEvent(
                    success=False, attempt=attempt, final_error="Retry cancelled"
                )
            )
            return False
        finally:
            self._session._retry_abort_event = None

        return True

    def abort_retry(self) -> None:
        """取消进行中的自动重试。"""
        if self._session._retry_abort_event is not None:
            self._session._retry_abort_event.abort()

    def set_auto_retry_enabled(self, enabled: bool) -> None:
        """开关自动重试设置。"""
        self._session.settings_manager.set_retry_enabled(enabled)
