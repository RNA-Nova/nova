"""上下文压缩控制。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from nova_ai import AbortController, Model

from nova_harness.core.config.auth.guidance import (
    format_no_auth_message,
    format_no_model_selected_message,
)
from nova_harness.core.harness.compaction import compaction as _compaction_module
from nova_harness.core.harness.compaction.compaction import (
    calculate_context_tokens,
    estimate_context_tokens,
    estimate_messages_tokens,
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


def _without_deleted_headers(
    headers: Optional[Dict[str, Optional[str]]],
) -> Optional[Dict[str, str]]:
    """过滤值为 None 的 header（None 表示抑制同名默认头）。

    对齐 TS agent-session 的 withoutDeletedHeaders。
    """
    if not headers:
        return None
    return {k: v for k, v in headers.items() if v is not None}


async def get_summarization_request_auth(
    session: AgentSessionProtocol, model: Model, *, required: bool
) -> Tuple[Optional[str], Optional[Dict[str, str]], Optional[Dict[str, str]]]:
    """解析摘要请求的鉴权，返回 (api_key, headers, env)。

    对齐 TS agent-session 的 ``_getRequiredRequestAuth`` /
    ``_getSummarizationRequestAuth``。Python 会话的 stream_fn 始终经
    ModelRuntime 的 auth 链解析（对应 TS 的默认 streamFn 分支），因此：

    - ``required=True``（手动压缩、分支摘要）：无 auth 时抛出引导性错误；
    - ``required=False``（自动压缩）：无 auth 时返回 ``(None, None, None)``，
      由调用方静默放弃。
    """
    result = await session.model_runtime.get_request_auth(model)
    # ModelAuth 是进程内 snake 契约（nova_ai types/auth.py 的 TypedDict）——
    # 曾误读 camel "apiKey"，导致压缩/分支摘要永远判为无鉴权
    api_key = result.auth.get("api_key") if result else None
    if api_key:
        return (
            api_key,
            _without_deleted_headers(result.auth.get("headers")),
            result.env,
        )
    if required:
        raise RuntimeError(
            format_no_auth_message(
                model.provider, session.model_runtime.is_using_oauth(model.provider)
            )
        )
    return None, None, None


def _to_epoch_ms(value: Any) -> float:
    """把时间戳统一为 epoch 毫秒（支持 int/float 毫秒与 ISO 字符串）。

    对齐 TS ``new Date(ts).getTime()``：naive ISO 按本地时区解释。
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp() * 1000
        except ValueError:
            return 0.0
    return 0.0


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
        """手动压缩会话上下文（先中止正在运行的 agent）。"""
        self._session._disconnect_from_agent()
        # 对齐 TS：disconnect → await abort → 发 compaction_start，
        # model/auth 检查在 try 内进行，保证 start/end 事件始终配对。
        await self._session.abort()
        self._session._compaction_abort_controller = AbortController("compact")
        self._session._emit(
            CompactionStartEvent(
                reason="manual", custom_instructions=custom_instructions
            )
        )

        try:
            if self._session.model is None:
                raise RuntimeError(format_no_model_selected_message())

            api_key, headers, env = await get_summarization_request_auth(
                self._session, self._session.model, required=True
            )

            path_entries = self._session.session_manager.get_branch()
            settings = self._session.settings_manager.get_compaction_settings()
            preparation = _compaction_module.prepare_compaction(path_entries, settings)
            if preparation is None:
                last_entry = path_entries[-1] if path_entries else None
                if last_entry is not None and last_entry.type == "compaction":
                    raise RuntimeError("Already compacted")
                raise RuntimeError("Nothing to compact (session too small)")

            extension_compaction: Any = None
            from_extension = False

            runner = self._session._extension_runner
            if runner is not None and runner.has_handlers(SESSION_BEFORE_COMPACT):
                from nova_harness.core.types.events import SessionBeforeCompactEvent

                result = await runner.emit(
                    SessionBeforeCompactEvent(
                        preparation=preparation,
                        branch_entries=path_entries,
                        custom_instructions=custom_instructions,
                    )
                )
                if getattr(result, "cancel", False):
                    raise RuntimeError("Compaction cancelled")
                extension_compaction = getattr(result, "compaction", None)
                if extension_compaction is not None:
                    from_extension = True

            summary: str
            first_kept_entry_id: str
            tokens_before: int
            details: Any

            if extension_compaction is not None:
                # 扩展提供了压缩内容
                summary = extension_compaction.summary
                first_kept_entry_id = extension_compaction.first_kept_entry_id
                tokens_before = extension_compaction.tokens_before
                details = getattr(extension_compaction, "details", None)
            else:
                result = await _compaction_module.compact(
                    preparation,
                    self._session.model,
                    api_key,
                    headers=headers,
                    custom_instructions=custom_instructions,
                    signal=self._session._compaction_abort_controller.signal,
                    thinking_level=self._session.thinking_level,
                    stream_fn=self._session.agent.stream_fn,
                    env=env,
                )
                summary = result.summary
                first_kept_entry_id = result.first_kept_entry_id
                tokens_before = result.tokens_before
                details = result.details

            if self._session._compaction_abort_controller.signal.aborted:
                raise RuntimeError("Compaction cancelled")

            compaction_entry = self._session.session_manager.append_compaction(
                summary, first_kept_entry_id, tokens_before, details, from_extension
            )
            new_messages = (
                self._session.session_manager.build_session_context().messages
            )
            self._session.agent.state.messages = new_messages

            compaction_result = CompactionResult(
                summary=summary,
                first_kept_entry_id=first_kept_entry_id,
                tokens_before=tokens_before,
                estimated_tokens_after=estimate_messages_tokens(new_messages),
                details=compaction_entry.details,
            )
            self._session._emit(
                CompactionEndEvent(
                    reason="manual",
                    result=compaction_result,
                    aborted=False,
                    will_retry=False,
                )
            )
            session_compact_event = SessionCompactEvent(
                compaction_entry=compaction_entry,
                from_extension=from_extension,
            )
            self._session._emit(session_compact_event)
            # 对齐 TS：session_compact 同时发给扩展（payload 为落盘的 CompactionEntry）
            if runner is not None:
                await runner.emit(session_compact_event)
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
        if not settings.enabled:
            return False

        if skip_aborted_check and assistant_message.stop_reason == "aborted":
            return False

        model = self._session.model
        context_window = model.context_window if model else 0

        # 如果 assistant 消息来自不同模型，跳过溢出检查
        same_model = (
            model is not None
            and assistant_message.provider == model.provider
            and assistant_message.model == model.id
        )

        # 如果消息时间早于最新压缩边界，跳过（防止陈旧 usage/错误重复触发）
        branch_entries = self._session.session_manager.get_branch()
        compaction_entry = get_latest_compaction_entry(branch_entries)
        if compaction_entry is not None and _to_epoch_ms(
            assistant_message.timestamp
        ) <= _to_epoch_ms(compaction_entry.timestamp):
            return False

        # 情况 1：上下文溢出。
        # 成功但超窗的响应只压不重试：回答已完成，agent.continue 无法从
        # assistant 消息续跑；错误响应才执行压缩+重试。
        if same_model and is_context_overflow(assistant_message, context_window):
            will_retry = assistant_message.stop_reason != "stop"
            if not will_retry:
                return await self.run_auto_compaction("overflow", will_retry=False)

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
            # 错误消息保留在会话文件中，但从上下文中移除以便重试
            messages = list(self._session.agent.state.messages)
            if messages and messages[-1].role == "assistant":
                self._session.agent.state.messages = messages[:-1]
            return await self.run_auto_compaction("overflow", will_retry=True)

        # 情况 2：达到阈值。
        # 错误消息或全零 usage 消息从最近一次有效响应估算，
        # 确保持续 API 错误（如 529）或畸形零 usage 响应也能触发压缩。
        usage = assistant_message.usage
        direct_tokens = calculate_context_tokens(usage) if usage is not None else 0
        if assistant_message.stop_reason == "error" or direct_tokens == 0:
            estimate = estimate_context_tokens(self._session.agent.state.messages)
            if estimate.last_usage_index is None:
                return False
            if compaction_entry is not None:
                usage_msg = self._session.agent.state.messages[
                    estimate.last_usage_index
                ]
                if usage_msg.role == "assistant" and _to_epoch_ms(
                    usage_msg.timestamp
                ) <= _to_epoch_ms(compaction_entry.timestamp):
                    return False
            context_tokens = estimate.tokens
        else:
            context_tokens = direct_tokens

        if should_compact(context_tokens, context_window, settings):
            return await self.run_auto_compaction("threshold", will_retry=False)

        return False

    async def run_auto_compaction(
        self,
        reason: str,
        will_retry: bool,
    ) -> bool:
        """内部：执行自动压缩并发射事件。

        对齐 TS：pre-start 检查（无模型/无鉴权/无可压缩内容）静默失败，
        只有真正开始压缩才发 compaction_start/end 事件对。
        """
        settings = self._session.settings_manager.get_compaction_settings()

        if self._session.model is None:
            return False

        api_key, headers, env = await get_summarization_request_auth(
            self._session, self._session.model, required=False
        )
        if not api_key:
            return False

        path_entries = self._session.session_manager.get_branch()
        preparation = _compaction_module.prepare_compaction(path_entries, settings)
        if preparation is None:
            return False

        self._session._emit(CompactionStartEvent(reason=reason))
        self._session._auto_compaction_abort_controller = AbortController(
            "auto_compact"
        )

        try:
            extension_compaction: Any = None
            from_extension = False

            runner = self._session._extension_runner
            if runner is not None and runner.has_handlers(SESSION_BEFORE_COMPACT):
                from nova_harness.core.types.events import SessionBeforeCompactEvent

                result = await runner.emit(
                    SessionBeforeCompactEvent(
                        preparation=preparation,
                        branch_entries=path_entries,
                        custom_instructions=None,
                    )
                )
                if getattr(result, "cancel", False):
                    self._session._emit(
                        CompactionEndEvent(
                            reason=reason, result=None, aborted=True, will_retry=False
                        )
                    )
                    return False
                extension_compaction = getattr(result, "compaction", None)
                if extension_compaction is not None:
                    from_extension = True

            summary: str
            first_kept_entry_id: str
            tokens_before: int
            details: Any

            if extension_compaction is not None:
                # 扩展提供了压缩内容
                summary = extension_compaction.summary
                first_kept_entry_id = extension_compaction.first_kept_entry_id
                tokens_before = extension_compaction.tokens_before
                details = getattr(extension_compaction, "details", None)
            else:
                result = await _compaction_module.compact(
                    preparation,
                    self._session.model,
                    api_key,
                    headers=headers,
                    signal=self._session._auto_compaction_abort_controller.signal,
                    thinking_level=self._session.thinking_level,
                    stream_fn=self._session.agent.stream_fn,
                    env=env,
                )
                summary = result.summary
                first_kept_entry_id = result.first_kept_entry_id
                tokens_before = result.tokens_before
                details = result.details

            if self._session._auto_compaction_abort_controller.signal.aborted:
                self._session._emit(
                    CompactionEndEvent(
                        reason=reason, result=None, aborted=True, will_retry=False
                    )
                )
                return False

            compaction_entry = self._session.session_manager.append_compaction(
                summary, first_kept_entry_id, tokens_before, details, from_extension
            )
            new_messages = (
                self._session.session_manager.build_session_context().messages
            )
            self._session.agent.state.messages = new_messages

            compaction_result = CompactionResult(
                summary=summary,
                first_kept_entry_id=first_kept_entry_id,
                tokens_before=tokens_before,
                estimated_tokens_after=estimate_messages_tokens(new_messages),
                details=compaction_entry.details,
            )
            self._session._emit(
                CompactionEndEvent(
                    reason=reason,
                    result=compaction_result,
                    aborted=False,
                    will_retry=will_retry,
                )
            )
            session_compact_event = SessionCompactEvent(
                compaction_entry=compaction_entry,
                from_extension=from_extension,
            )
            self._session._emit(session_compact_event)
            # 对齐 TS：session_compact 同时发给扩展（payload 为落盘的 CompactionEntry）
            if runner is not None:
                await runner.emit(session_compact_event)

            if will_retry:
                # 溢出/截断响应在 message_end 时已落盘，压缩重建状态可能把
                # 这条保留条目还原成 assistant 尾——continue_() 拒绝从
                # assistant 尾续跑，重试前再剥一次（error 与 length 同罪）
                messages = list(self._session.agent.state.messages)
                last_msg = messages[-1] if messages else None
                if (
                    last_msg is not None
                    and last_msg.role == "assistant"
                    and last_msg.stop_reason in ("error", "length")
                ):
                    self._session.agent.state.messages = messages[:-1]
                return True

            # 自动压缩完成时可能有 follow-up/steering/custom 消息在等待，
            # 返回 True 让队列中的消息继续投递。
            return self._session.agent.has_queued_messages()
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
