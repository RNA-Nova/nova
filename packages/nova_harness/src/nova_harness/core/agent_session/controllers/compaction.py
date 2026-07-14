"""上下文压缩控制。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from nova_agent import AbortController

from nova_harness.core.harness.compaction import compaction as _compaction_module
from nova_harness.core.harness.compaction.compaction import (
    calculate_context_tokens,
    estimate_context_tokens,
    should_compact,
)
from nova_harness.core.harness.session.utils import get_latest_compaction_entry
from nova_harness.core.types.compaction import CompactionResult
from nova_harness.core.types.events import (
    CompactionEndEvent,
    CompactionStartEvent,
    SessionCompactEvent,
)
from nova_harness.core.types.events.constants import SESSION_BEFORE_COMPACT
from nova_harness.core.types.protocols import AgentSessionProtocol
from nova_harness.core.utils import is_context_overflow

if TYPE_CHECKING:
    from nova_ai import AssistantMessage


class CompactionController:
    """封装 AgentSession 的手动/自动压缩逻辑。"""

    def __init__(self, session: AgentSessionProtocol) -> None:
        self._session = session

    @property
    def is_compacting(self) -> bool:
        return (
            self._session._compaction_abort_controller is not None
            or self._session._auto_compaction_abort_controller is not None
            or self._session._branch_summary_abort_controller is not None
        )

    def abort_compaction(self) -> None:
        """取消进行中的压缩（手动或自动）。"""
        if self._session._compaction_abort_controller is not None:
            self._session._compaction_abort_controller.abort()
        if self._session._auto_compaction_abort_controller is not None:
            self._session._auto_compaction_abort_controller.abort()

    def abort_branch_summary(self) -> None:
        """取消进行中的分支摘要。"""
        if self._session._branch_summary_abort_controller is not None:
            self._session._branch_summary_abort_controller.abort()

    def set_auto_compaction_enabled(self, enabled: bool) -> None:
        """开关自动压缩设置。"""
        self._session.settings_manager.set_compaction_enabled(enabled)

    async def compact(
        self, custom_instructions: Optional[str] = None
    ) -> CompactionResult:
        """手动压缩会话上下文。"""
        if self._session.model is None:
            raise RuntimeError("No model selected")

        api_key = None
        registry = self._session.model_registry
        if hasattr(registry, "get_api_key"):
            api_key = await registry.get_api_key(self._session.model)
        if not api_key:
            raise RuntimeError(f"No API key for {self._session.model.provider}")

        self._session._disconnect_from_agent()
        self._session._compaction_abort_controller = AbortController("compact")
        self._session._emit(
            CompactionStartEvent(
                reason="manual", custom_instructions=custom_instructions
            )
        )

        try:
            path_entries = self._session.session_manager.get_branch()
            settings = self._session.settings_manager.get_compaction_settings()
            preparation = _compaction_module.prepare_compaction(path_entries, settings)
            if preparation is None:
                last_entry = path_entries[-1] if path_entries else None
                if getattr(last_entry, "type", None) == "compaction":
                    raise RuntimeError("Already compacted")
                raise RuntimeError("Nothing to compact (session too small)")

            summary: str
            first_kept_entry_id: str
            tokens_before: int
            details: Any
            from_extension = False

            runner = self._session._extension_runner
            if runner is not None and runner.has_handlers(SESSION_BEFORE_COMPACT):
                from nova_harness.core.types.events import SessionBeforeCompactEvent

                result = await runner.emit(
                    SessionBeforeCompactEvent(
                        preparation=preparation,
                        branch_entries=path_entries,
                        custom_instructions=custom_instructions,
                        signal=self._session._compaction_abort_controller.signal,
                    )
                )
                if getattr(result, "cancel", False):
                    raise RuntimeError("Compaction cancelled")
                ext_result = getattr(result, "compaction", None)
                if ext_result is not None:
                    summary = ext_result.summary
                    first_kept_entry_id = ext_result.first_kept_entry_id
                    tokens_before = ext_result.tokens_before
                    details = getattr(ext_result, "details", None)
                    from_extension = True
                else:
                    result = await _compaction_module.compact(
                        preparation,
                        self._session.model,
                        api_key,
                        custom_instructions=custom_instructions,
                        signal=self._session._compaction_abort_controller.signal,
                        thinking_level=self._session.thinking_level,
                    )
                    summary = result.summary
                    first_kept_entry_id = result.first_kept_entry_id
                    tokens_before = result.tokens_before
                    details = getattr(result, "details", None)
            else:
                result = await _compaction_module.compact(
                    preparation,
                    self._session.model,
                    api_key,
                    custom_instructions=custom_instructions,
                    signal=self._session._compaction_abort_controller,
                    thinking_level=self._session.thinking_level,
                )
                summary = result.summary
                first_kept_entry_id = result.first_kept_entry_id
                tokens_before = result.tokens_before
                details = getattr(result, "details", None)

            if self._session._compaction_abort_controller.signal.aborted:
                raise RuntimeError("Compaction cancelled")

            self._session.session_manager.append_compaction(
                summary, first_kept_entry_id, tokens_before, details, from_extension
            )
            self._session.agent.state.messages = (
                self._session.session_manager.build_session_context().messages
            )

            compaction_result = CompactionResult(
                summary=summary,
                first_kept_entry_id=first_kept_entry_id,
                tokens_before=tokens_before,
                details=details,
            )
            self._session._emit(
                CompactionEndEvent(
                    reason="manual",
                    result=compaction_result,
                    aborted=False,
                    will_retry=False,
                )
            )
            self._session._emit(
                SessionCompactEvent(
                    compaction_entry=compaction_result,
                    from_extension=from_extension,
                )
            )
            return compaction_result
        except Exception as error:
            message = str(error)
            aborted = message == "Compaction cancelled"
            self._session._emit(
                CompactionEndEvent(
                    reason="manual",
                    result=None,
                    aborted=aborted,
                    will_retry=False,
                    error_message=None if aborted else f"Compaction failed: {message}",
                )
            )
            raise
        finally:
            self._session._compaction_abort_controller = None
            self._session._reconnect_to_agent()

    async def check_compaction(
        self,
        assistant_message: "AssistantMessage",
        skip_aborted_check: bool = True,
    ) -> bool:
        """检查是否需要自动压缩并在需要时执行。"""
        settings = self._session.settings_manager.get_compaction_settings()
        if not getattr(settings, "enabled", False):
            return False

        if (
            skip_aborted_check
            and getattr(assistant_message, "stop_reason", None) == "aborted"
        ):
            return False

        context_window = getattr(self._session.model, "context_window", 0) or 0

        # 如果 assistant 消息来自不同模型，跳过溢出检查
        same_model = (
            self._session.model is not None
            and getattr(assistant_message, "provider", None)
            == self._session.model.provider
            and getattr(assistant_message, "model", None) == self._session.model.id
        )

        # 如果消息时间早于最新压缩边界，跳过
        branch_entries = self._session.session_manager.get_branch()
        compaction_entry = get_latest_compaction_entry(branch_entries)
        assistant_timestamp = getattr(assistant_message, "timestamp", 0) or 0
        if compaction_entry is not None and assistant_timestamp <= getattr(
            compaction_entry, "timestamp", 0
        ):
            return False

        # 情况 1：上下文溢出
        if same_model and is_context_overflow(assistant_message, context_window):
            if self._session._overflow_recovery_attempted:
                self._session._emit(
                    CompactionEndEvent(
                        reason="overflow",
                        result=None,
                        aborted=False,
                        will_retry=False,
                        error_message=(
                            "Context overflow recovery failed after one compact-and-retry attempt. "
                            "Try reducing context or switching to a larger-context model."
                        ),
                    )
                )
                return False
            self._session._overflow_recovery_attempted = True
            messages = list(self._session.agent.state.messages)
            if messages and getattr(messages[-1], "role", None) == "assistant":
                self._session.agent.state.messages = messages[:-1]
            return await self.run_auto_compaction("overflow", will_retry=True)

        # 情况 2：达到阈值
        context_tokens: int
        if getattr(assistant_message, "stop_reason", None) == "error":
            estimate = estimate_context_tokens(self._session.agent.state.messages)
            if estimate.last_usage_index is None:
                return False
            if compaction_entry is not None:
                usage_msg = self._session.agent.state.messages[
                    estimate.last_usage_index
                ]
                if getattr(usage_msg, "role", None) == "assistant" and getattr(
                    usage_msg, "timestamp", 0
                ) <= getattr(compaction_entry, "timestamp", 0):
                    return False
            context_tokens = estimate.tokens
        else:
            usage = getattr(assistant_message, "usage", None)
            if usage is None:
                return False
            context_tokens = calculate_context_tokens(usage)

        if should_compact(context_tokens, context_window, settings):
            return await self.run_auto_compaction("threshold", will_retry=False)

        return False

    async def run_auto_compaction(
        self,
        reason: str,
        will_retry: bool,
    ) -> bool:
        """内部：执行自动压缩并发射事件。"""
        settings = self._session.settings_manager.get_compaction_settings()
        self._session._emit(CompactionStartEvent(reason=reason))
        self._session._auto_compaction_abort_controller = AbortController(
            "auto_compact"
        )

        try:
            if self._session.model is None:
                self._session._emit(
                    CompactionEndEvent(
                        reason=reason, result=None, aborted=False, will_retry=False
                    )
                )
                return False

            api_key = None
            registry = self._session.model_registry
            if hasattr(registry, "get_api_key"):
                api_key = await registry.get_api_key(self._session.model)
            if not api_key:
                self._session._emit(
                    CompactionEndEvent(
                        reason=reason, result=None, aborted=False, will_retry=False
                    )
                )
                return False

            path_entries = self._session.session_manager.get_branch()
            preparation = _compaction_module.prepare_compaction(path_entries, settings)
            if preparation is None:
                self._session._emit(
                    CompactionEndEvent(
                        reason=reason, result=None, aborted=False, will_retry=False
                    )
                )
                return False

            summary: str
            first_kept_entry_id: str
            tokens_before: int
            details: Any
            from_extension = False

            runner = self._session._extension_runner
            if runner is not None and runner.has_handlers(SESSION_BEFORE_COMPACT):
                from nova_harness.core.types.events import SessionBeforeCompactEvent

                result = await runner.emit(
                    SessionBeforeCompactEvent(
                        preparation=preparation,
                        branch_entries=path_entries,
                        custom_instructions=None,
                        signal=self._session._auto_compaction_abort_controller.signal,
                    )
                )
                if getattr(result, "cancel", False):
                    self._session._emit(
                        CompactionEndEvent(
                            reason=reason, result=None, aborted=True, will_retry=False
                        )
                    )
                    return False
                ext_result = getattr(result, "compaction", None)
                if ext_result is not None:
                    summary = ext_result.summary
                    first_kept_entry_id = ext_result.first_kept_entry_id
                    tokens_before = ext_result.tokens_before
                    details = getattr(ext_result, "details", None)
                    from_extension = True
                else:
                    result = await _compaction_module.compact(
                        preparation,
                        self._session.model,
                        api_key,
                        signal=self._session._auto_compaction_abort_controller.signal,
                        thinking_level=self._session.thinking_level,
                    )
                    summary = result.summary
                    first_kept_entry_id = result.first_kept_entry_id
                    tokens_before = result.tokens_before
                    details = getattr(result, "details", None)
            else:
                result = await _compaction_module.compact(
                    preparation,
                    self._session.model,
                    api_key,
                    signal=self._session._auto_compaction_abort_controller.signal,
                    thinking_level=self._session.thinking_level,
                )
                summary = result.summary
                first_kept_entry_id = result.first_kept_entry_id
                tokens_before = result.tokens_before
                details = getattr(result, "details", None)

            if self._session._auto_compaction_abort_controller.signal.aborted:
                self._session._emit(
                    CompactionEndEvent(
                        reason=reason, result=None, aborted=True, will_retry=False
                    )
                )
                return False

            self._session.session_manager.append_compaction(
                summary, first_kept_entry_id, tokens_before, details, from_extension
            )
            self._session.agent.state.messages = (
                self._session.session_manager.build_session_context().messages
            )

            compaction_result = CompactionResult(
                summary=summary,
                first_kept_entry_id=first_kept_entry_id,
                tokens_before=tokens_before,
                details=details,
            )
            self._session._emit(
                CompactionEndEvent(
                    reason=reason,
                    result=compaction_result,
                    aborted=False,
                    will_retry=will_retry,
                )
            )
            self._session._emit(
                SessionCompactEvent(
                    compaction_entry=compaction_result,
                    from_extension=from_extension,
                )
            )

            if will_retry:
                messages = list(self._session.agent.state.messages)
                last_msg = messages[-1] if messages else None
                if (
                    last_msg is not None
                    and getattr(last_msg, "role", None) == "assistant"
                    and getattr(last_msg, "stop_reason", None) == "error"
                ):
                    self._session.agent.state.messages = messages[:-1]
                return True

            return bool(
                hasattr(self._session.agent, "has_queued_messages")
                and self._session.agent.has_queued_messages()
            )
        except Exception as error:
            error_message = str(error)
            self._session._emit(
                CompactionEndEvent(
                    reason=reason,
                    result=None,
                    aborted=False,
                    will_retry=False,
                    error_message=(
                        f"Context overflow recovery failed: {error_message}"
                        if reason == "overflow"
                        else f"Auto-compaction failed: {error_message}"
                    ),
                )
            )
            return False
        finally:
            self._session._auto_compaction_abort_controller = None
