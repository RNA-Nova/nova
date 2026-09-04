"""CompactionController 编排行为测试（对齐 TS agent-session 语义）。

覆盖本轮修复的编排层问题：
- 时间戳 int(ms) vs ISO str 比较（原 TypeError）
- 成功但超窗的响应只压缩不重试
- usage 缺失/全零时走估算路径
- tree 导航的 abort controller 清理
"""

from datetime import datetime, timedelta
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from nova_ai import AssistantMessage, Model, ModelCost, Usage
from nova_harness.core.agent_session.controllers.compaction import CompactionController
from nova_harness.core.agent_session.controllers.tree import TreeNavigator
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.types.compaction import CompactionSettings


def _model(context_window: int = 1000) -> Model:
    return Model(
        id="m",
        name="m",
        api="openai-completions",
        provider="test",
        base_url="",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0),
        context_window=context_window,
        max_tokens=4096,
    )


def _assistant(
    text: str,
    *,
    stop_reason: str = "stop",
    usage: Optional[Usage] = None,
    timestamp: int = 0,
) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[{"type": "text", "text": text}],
        api="openai-completions",
        provider="test",
        model="m",
        # nova_ai 模型中 usage 总是对象；"无 usage" 即全零 Usage
        usage=usage if usage is not None else Usage(),
        stop_reason=stop_reason,
        timestamp=timestamp,
    )


def _session(tmp_path, model, messages, *, compaction_enabled=True):
    session = MagicMock()
    session.model = model
    session.thinking_level = None
    session._extension_runner = None
    session._overflow_recovery_attempted = False
    session._branch_summary_abort_controller = None
    session.settings_manager.get_compaction_settings.return_value = CompactionSettings(
        enabled=compaction_enabled, reserve_tokens=10, keep_recent_tokens=10
    )
    session.session_manager = SessionManager.create("/tmp/cwd", str(tmp_path))
    session.agent.state.messages = list(messages)
    return session


@pytest.mark.asyncio
async def test_stale_check_with_compaction_entry_no_type_error(tmp_path):
    """分支上存在 compaction 条目（ISO 时间戳）时，
    与 assistant 的 int 毫秒时间戳比较不抛 TypeError，且正确判为过期。"""
    model = _model()
    session = _session(tmp_path, model, [])
    session.session_manager.append_message(_assistant("old", timestamp=1000))
    session.session_manager.append_compaction("summary", "x", 100)

    controller = CompactionController(session)
    controller.run_auto_compaction = AsyncMock(return_value=True)

    stale = _assistant("old", timestamp=1000)
    result = await controller.check_compaction(stale)

    assert result is False
    controller.run_auto_compaction.assert_not_called()


@pytest.mark.asyncio
async def test_fresh_message_after_compaction_proceeds(tmp_path):
    """压缩边界之后的新消息不被误判为过期。"""
    model = _model()
    session = _session(tmp_path, model, [])
    session.session_manager.append_message(_assistant("old", timestamp=1000))
    session.session_manager.append_compaction("summary", "x", 100)

    controller = CompactionController(session)
    controller.run_auto_compaction = AsyncMock(return_value=True)

    future_ms = int((datetime.now() + timedelta(hours=1)).timestamp() * 1000)
    fresh = _assistant("new", usage=Usage(input=995, output=10), timestamp=future_ms)
    result = await controller.check_compaction(fresh)

    assert result is True
    controller.run_auto_compaction.assert_called_once_with(
        "threshold", will_retry=False
    )


@pytest.mark.asyncio
async def test_successful_overflow_compacts_without_retry(tmp_path):
    """成功（stop）但 usage 超窗：只压缩不重试、不删消息、不标记 recovery。"""
    model = _model(context_window=100)
    big_usage = Usage(input=150, output=10)
    message = _assistant("done", usage=big_usage)
    session = _session(tmp_path, model, [message])

    controller = CompactionController(session)
    controller.run_auto_compaction = AsyncMock(return_value=True)

    result = await controller.check_compaction(message)

    assert result is True
    controller.run_auto_compaction.assert_called_once_with("overflow", will_retry=False)
    assert session.agent.state.messages == [message]
    assert not session._overflow_recovery_attempted


@pytest.mark.asyncio
async def test_error_overflow_retries_once(tmp_path):
    """错误（error）且超窗：压缩+重试，错误消息从上下文移除。"""
    model = _model(context_window=100)
    message = _assistant(
        "failed", stop_reason="error", usage=Usage(input=150, output=10)
    )
    session = _session(tmp_path, model, [message])
    session._overflow_recovery_attempted = False

    controller = CompactionController(session)
    controller.run_auto_compaction = AsyncMock(return_value=True)

    result = await controller.check_compaction(message)

    assert result is True
    controller.run_auto_compaction.assert_called_once_with("overflow", will_retry=True)
    assert session.agent.state.messages == []
    assert session._overflow_recovery_attempted is True


@pytest.mark.asyncio
async def test_zero_usage_falls_back_to_estimation(tmp_path):
    """usage 缺失/全零（如 529 错误页）不再直接返回 False：
    走最近有效响应的估算路径。"""
    model = _model(context_window=100)
    prev = _assistant("prev", usage=Usage(input=90, output=5), timestamp=1)
    session = _session(tmp_path, model, [prev])

    controller = CompactionController(session)
    controller.run_auto_compaction = AsyncMock(return_value=True)

    zero = _assistant("zero", usage=Usage(), timestamp=2)
    result = await controller.check_compaction(zero)

    assert result is True
    controller.run_auto_compaction.assert_called_once_with(
        "threshold", will_retry=False
    )


@pytest.mark.asyncio
async def test_tree_navigate_without_summary_clears_abort_controller(tmp_path):
    """不做摘要的树导航后，branch summary abort controller 必须清理
    （否则 is_compacting 永久为 True）。"""
    from nova_ai import UserMessage

    session = _session(tmp_path, _model(), [])
    first = session.session_manager.append_message(
        UserMessage(role="user", content="first", timestamp=1)
    )
    session.session_manager.append_message(
        UserMessage(role="user", content="second", timestamp=2)
    )

    # 导航回更早的节点（不等于当前 leaf，走完整的 controller set/clear 路径）
    navigator = TreeNavigator(session)
    await navigator.navigate(first, {"summarize": False})

    assert session._branch_summary_abort_controller is None


# ---------------------------------------------------------------------------
# get_summarization_request_auth（对齐 TS _getRequiredRequestAuth 语义）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarization_auth_returns_headers_filtered_and_env(tmp_path):
    """有 auth 时返回 (api_key, headers, env)，headers 中 None 值被过滤
    （None 表示抑制同名默认头，对齐 TS withoutDeletedHeaders）。"""
    from nova_ai import AuthResult
    from nova_harness.core.agent_session.controllers.compaction import (
        get_summarization_request_auth,
    )

    session = _session(tmp_path, _model(), [])
    # ModelAuth 为进程内 snake 契约（nova_ai types/auth.py TypedDict：
    # api_key/headers/base_url）——本测试曾用 camel "apiKey" 编写 mock,
    # 把实现里的误读掩护成了"通过"（测试拉偏方向的实例）
    session.model_runtime.get_request_auth = AsyncMock(
        return_value=AuthResult(
            auth={"api_key": "k", "headers": {"X-A": "1", "X-B": None}},
            env={"FOO": "bar"},
        )
    )

    api_key, headers, env = await get_summarization_request_auth(
        session, session.model, required=True
    )

    assert api_key == "k"
    assert headers == {"X-A": "1"}
    assert env == {"FOO": "bar"}


@pytest.mark.asyncio
async def test_summarization_auth_required_raises_optional_silent(tmp_path):
    """required=True 无 auth 抛引导性错误（OAuth provider 给 /login 文案）；
    required=False 静默返回 (None, None, None)。"""
    from nova_harness.core.agent_session.controllers.compaction import (
        get_summarization_request_auth,
    )

    session = _session(tmp_path, _model(), [])
    session.model_runtime.get_request_auth = AsyncMock(return_value=None)
    session.model_runtime.is_using_oauth.return_value = True

    with pytest.raises(RuntimeError, match="/login test"):
        await get_summarization_request_auth(session, session.model, required=True)

    assert await get_summarization_request_auth(
        session, session.model, required=False
    ) == (None, None, None)


# ---------------------------------------------------------------------------
# 手动/自动压缩的 auth 前置行为与事件配对
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_compact_no_model_emits_start_end_pair(tmp_path):
    """无模型时手动压缩仍保证 compaction_start/end 事件配对（对齐 TS：
    检查在 emit start 之后、try 之内进行）。"""
    session = _session(tmp_path, None, [])
    session.abort = AsyncMock()

    controller = CompactionController(session)
    with pytest.raises(RuntimeError, match="No model selected"):
        await controller.compact()

    emitted = [c.args[0] for c in session._emit.call_args_list]
    assert [e.type for e in emitted] == ["compaction_start", "compaction_end"]
    assert emitted[1].error_message is not None


@pytest.mark.asyncio
async def test_manual_compact_without_auth_emits_error_end(tmp_path):
    """无 auth 时手动压缩：compaction_start 后紧跟带引导文案的 compaction_end。"""
    session = _session(tmp_path, _model(), [])
    session.abort = AsyncMock()
    session.model_runtime.get_request_auth = AsyncMock(return_value=None)
    session.model_runtime.is_using_oauth.return_value = False

    controller = CompactionController(session)
    with pytest.raises(RuntimeError, match="No API key found"):
        await controller.compact()

    emitted = [c.args[0] for c in session._emit.call_args_list]
    assert [e.type for e in emitted] == ["compaction_start", "compaction_end"]
    assert "No API key found" in emitted[1].error_message


@pytest.mark.asyncio
async def test_auto_compaction_without_auth_silent_false(tmp_path):
    """自动压缩无 auth：静默返回 False，不发任何事件。"""
    session = _session(tmp_path, _model(), [])
    session.model_runtime.get_request_auth = AsyncMock(return_value=None)

    controller = CompactionController(session)
    result = await controller.run_auto_compaction("threshold", will_retry=False)

    assert result is False
    session._emit.assert_not_called()


@pytest.mark.asyncio
async def test_auto_compaction_passes_stream_fn_headers_env(tmp_path, monkeypatch):
    """自动压缩调用算法层 compact() 时必须透传会话的 stream_fn、
    provider headers 与 env（对齐 TS：摘要请求保持 SDK 请求行为一致）。"""
    from types import SimpleNamespace

    from nova_ai import AuthResult
    from nova_harness.core.types.compaction import (
        CompactionPreparation,
        CompactionResult,
    )

    session = _session(tmp_path, _model(), [])
    session.model_runtime.get_request_auth = AsyncMock(
        return_value=AuthResult(
            auth={"api_key": "k", "headers": {"X-A": "1"}}, env={"E": "v"}
        )
    )
    session.agent.has_queued_messages.return_value = False

    # 隔离算法层与 SessionManager：只验证 controller 的传参与事件流
    prep = CompactionPreparation(
        first_kept_entry_id="entry-1",
        messages_to_summarize=[],
        turn_prefix_messages=[],
        is_split_turn=False,
        tokens_before=100,
    )
    monkeypatch.setattr(
        "nova_harness.core.agent_session.controllers.compaction."
        "_compaction_module.prepare_compaction",
        lambda entries, settings: prep,
    )
    fake_compact = AsyncMock(
        return_value=CompactionResult(
            summary="s", first_kept_entry_id="entry-1", tokens_before=100
        )
    )
    monkeypatch.setattr(
        "nova_harness.core.agent_session.controllers.compaction."
        "_compaction_module.compact",
        fake_compact,
    )
    session.session_manager = MagicMock()
    session.session_manager.build_session_context.return_value = SimpleNamespace(
        messages=[]
    )

    controller = CompactionController(session)
    result = await controller.run_auto_compaction("threshold", will_retry=False)

    assert result is False
    fake_compact.assert_awaited_once()
    kwargs = fake_compact.call_args.kwargs
    assert kwargs["headers"] == {"X-A": "1"}
    assert kwargs["env"] == {"E": "v"}
    assert kwargs["stream_fn"] is session.agent.stream_fn
