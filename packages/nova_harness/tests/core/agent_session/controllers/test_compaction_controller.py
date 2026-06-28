"""
CompactionController 单元测试。

覆盖手动压缩、自动压缩触发、取消与扩展 hook 路径。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_harness.core.types.compaction import CompactionResult
from nova_harness.core.types.events import (
    CompactionEndEvent,
    CompactionStartEvent,
    SessionCompactEvent,
)


@pytest.fixture
def compact_session(make_agent_session):
    """构造一个用于测试压缩控制器的 session，默认启用重试/压缩。"""
    sess = make_agent_session()
    runner = MagicMock()
    runner.has_handlers = MagicMock(return_value=False)
    runner.emit = AsyncMock(return_value=None)
    sess._extension_runner = runner
    sess.settings_manager.get_compaction_settings.return_value = MagicMock(
        enabled=True,
        reserve_tokens=16384,
        keep_recent_tokens=1000,
    )
    sess.settings_manager.get_branch_summary_settings.return_value = MagicMock(
        reserve_tokens=16384
    )
    return sess


def _make_preparation():
    return MagicMock(
        first_kept_entry_id="e1",
        messages_to_summarize=[],
        turn_prefix_messages=[],
        is_split_turn=False,
        tokens_before=100,
    )


def _make_compact_result():
    return MagicMock(
        summary="summary",
        first_kept_entry_id="e1",
        tokens_before=100,
        details=None,
    )


# ---------------------------------------------------------------------------
# 状态与设置
# ---------------------------------------------------------------------------


def test_is_compacting_reflects_controllers(compact_session):
    """is_compacting 应在任一 abort controller 存在时返回 True。"""
    assert compact_session._compaction.is_compacting is False
    compact_session._compaction_abort_controller = MagicMock()
    assert compact_session._compaction.is_compacting is True
    compact_session._compaction_abort_controller = None
    compact_session._auto_compaction_abort_controller = MagicMock()
    assert compact_session._compaction.is_compacting is True


def test_abort_compaction_sets_flags(compact_session):
    """abort_compaction 应把所有存在的 controller 标记为 aborted。"""
    manual = MagicMock()
    auto = MagicMock()
    compact_session._compaction_abort_controller = manual
    compact_session._auto_compaction_abort_controller = auto
    compact_session._compaction.abort_compaction()
    assert manual.aborted is True
    assert auto.aborted is True


def test_abort_branch_summary(compact_session):
    """abort_branch_summary 应标记 branch summary controller。"""
    ctrl = MagicMock()
    compact_session._branch_summary_abort_controller = ctrl
    compact_session._compaction.abort_branch_summary()
    assert ctrl.aborted is True


def test_set_auto_compaction_enabled(compact_session):
    """set_auto_compaction_enabled 应委托给 settings_manager。"""
    compact_session._compaction.set_auto_compaction_enabled(True)
    compact_session.settings_manager.set_compaction_enabled.assert_called_once_with(
        True
    )


# ---------------------------------------------------------------------------
# compact() 主路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compact_raises_when_no_model(compact_session):
    """未选择模型时应抛错。"""
    compact_session.agent.state.model = None
    with pytest.raises(RuntimeError, match="No model selected"):
        await compact_session._compaction.compact()


@pytest.mark.asyncio
async def test_compact_raises_when_no_api_key(compact_session):
    """没有 API key 时应抛错。"""
    compact_session.model_registry.get_api_key = AsyncMock(return_value="")
    with pytest.raises(RuntimeError, match="No API key"):
        await compact_session._compaction.compact()


@pytest.mark.asyncio
async def test_compact_raises_already_compacted(compact_session):
    """prepare_compaction 返回 None 且最后一条是 compaction 时提示已压缩。"""
    compact_session.session_manager.get_branch.return_value = [
        MagicMock(type="compaction")
    ]
    with patch(
        "nova_harness.core.agent_session.controllers.compaction._compaction_module.prepare_compaction",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="Already compacted"):
            await compact_session._compaction.compact()


@pytest.mark.asyncio
async def test_compact_raises_nothing_to_compact(compact_session):
    """prepare_compaction 返回 None 且会话太小时提示无内容可压缩。"""
    compact_session.session_manager.get_branch.return_value = []
    with patch(
        "nova_harness.core.agent_session.controllers.compaction._compaction_module.prepare_compaction",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="Nothing to compact"):
            await compact_session._compaction.compact()


@pytest.mark.asyncio
async def test_compact_success_emits_events_and_updates_messages(compact_session):
    """压缩成功后应发射事件并刷新 agent messages。"""
    compact_session.session_manager.get_branch.return_value = []
    compact_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[MagicMock()]
    )
    events = []
    compact_session.subscribe(events.append)

    with (
        patch(
            "nova_harness.core.agent_session.controllers.compaction._compaction_module.prepare_compaction",
            return_value=_make_preparation(),
        ),
        patch(
            "nova_harness.core.agent_session.controllers.compaction._compaction_module.compact",
            AsyncMock(return_value=_make_compact_result()),
        ),
    ):
        result = await compact_session._compaction.compact("custom")

    assert isinstance(result, CompactionResult)
    assert result.summary == "summary"
    compact_session.session_manager.append_compaction.assert_called_once()
    start_events = [e for e in events if isinstance(e, CompactionStartEvent)]
    end_events = [e for e in events if isinstance(e, CompactionEndEvent)]
    compact_events = [e for e in events if isinstance(e, SessionCompactEvent)]
    assert len(start_events) == 1
    assert len(end_events) == 1
    assert end_events[0].aborted is False
    assert len(compact_events) == 1
    assert compact_session.agent.state.messages == [
        compact_session.session_manager.build_session_context.return_value.messages[0]
    ]


@pytest.mark.asyncio
async def test_compact_aborted_during_execution(compact_session):
    """压缩过程中被取消应抛错并发射 aborted 事件。"""
    compact_session.session_manager.get_branch.return_value = []
    events = []
    compact_session.subscribe(events.append)

    async def _cancel_and_return(*args, **kwargs):
        compact_session._compaction_abort_controller.aborted = True
        return _make_compact_result()

    with (
        patch(
            "nova_harness.core.agent_session.controllers.compaction._compaction_module.prepare_compaction",
            return_value=_make_preparation(),
        ),
        patch(
            "nova_harness.core.agent_session.controllers.compaction._compaction_module.compact",
            AsyncMock(side_effect=_cancel_and_return),
        ),
    ):
        with pytest.raises(RuntimeError, match="Compaction cancelled"):
            await compact_session._compaction.compact()

    end_events = [e for e in events if isinstance(e, CompactionEndEvent)]
    assert end_events[-1].aborted is True


@pytest.mark.asyncio
async def test_compact_extension_result_takes_priority(compact_session):
    """session_before_compact 返回 compaction 时应使用扩展结果。"""
    compact_session.session_manager.get_branch.return_value = []
    compact_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[]
    )
    ext_result = MagicMock(
        summary="ext-summary",
        first_kept_entry_id="ext-1",
        tokens_before=42,
        details={"x": 1},
    )
    compact_session._extension_runner.has_handlers.return_value = True
    compact_session._extension_runner.emit = AsyncMock(
        return_value=MagicMock(cancel=False, compaction=ext_result)
    )

    with patch(
        "nova_harness.core.agent_session.controllers.compaction._compaction_module.prepare_compaction",
        return_value=_make_preparation(),
    ):
        result = await compact_session._compaction.compact()

    assert result.summary == "ext-summary"
    compact_session.session_manager.append_compaction.assert_called_once_with(
        "ext-summary", "ext-1", 42, {"x": 1}, True
    )


@pytest.mark.asyncio
async def test_compact_extension_cancelled(compact_session):
    """session_before_compact 返回 cancel 时应取消压缩。"""
    compact_session.session_manager.get_branch.return_value = []
    compact_session._extension_runner.has_handlers.return_value = True
    compact_session._extension_runner.emit = AsyncMock(
        return_value=MagicMock(cancel=True)
    )

    with patch(
        "nova_harness.core.agent_session.controllers.compaction._compaction_module.prepare_compaction",
        return_value=_make_preparation(),
    ):
        with pytest.raises(RuntimeError, match="Compaction cancelled"):
            await compact_session._compaction.compact()


# ---------------------------------------------------------------------------
# check_compaction() 自动触发
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_compaction_disabled_returns_false(compact_session):
    """压缩禁用时直接返回 False。"""
    compact_session.settings_manager.get_compaction_settings.return_value = MagicMock(
        enabled=False
    )
    msg = MagicMock()
    assert await compact_session._compaction.check_compaction(msg) is False


@pytest.mark.asyncio
async def test_check_compaction_skips_aborted_stop_reason(compact_session):
    """skip_aborted_check 为 True 且 stop_reason=aborted 时跳过。"""
    msg = MagicMock(stop_reason="aborted")
    assert await compact_session._compaction.check_compaction(msg) is False


@pytest.mark.asyncio
async def test_check_compaction_overflow_first_time_triggers_auto(compact_session):
    """上下文溢出首次触发自动压缩并 retry。"""
    model = compact_session.agent.state.model
    model.provider = "test"
    model.id = "test-model"
    msg = MagicMock(
        stop_reason="error",
        provider="test",
        model="test-model",
        usage=None,
    )
    compact_session.agent.state.messages = [
        MagicMock(role="assistant", stop_reason="error")
    ]

    with (
        patch(
            "nova_harness.core.agent_session.controllers.compaction.is_context_overflow",
            return_value=True,
        ),
        patch.object(
            compact_session._compaction,
            "run_auto_compaction",
            AsyncMock(return_value=True),
        ),
    ):
        assert await compact_session._compaction.check_compaction(msg) is True
        assert compact_session._overflow_recovery_attempted is True
        compact_session._compaction.run_auto_compaction.assert_awaited_once_with(
            "overflow", will_retry=True
        )


@pytest.mark.asyncio
async def test_check_compaction_overflow_second_attempt_fails(compact_session):
    """上下文溢出第二次尝试应报错并不再重试。"""
    compact_session._overflow_recovery_attempted = True
    model = compact_session.agent.state.model
    model.provider = "test"
    model.id = "test-model"
    msg = MagicMock(
        stop_reason="error",
        provider="test",
        model="test-model",
        usage=None,
    )
    events = []
    compact_session.subscribe(events.append)

    with patch(
        "nova_harness.core.agent_session.controllers.compaction.is_context_overflow",
        return_value=True,
    ):
        assert await compact_session._compaction.check_compaction(msg) is False

    end_events = [e for e in events if isinstance(e, CompactionEndEvent)]
    assert len(end_events) == 1
    assert "recovery failed" in end_events[0].error_message


@pytest.mark.asyncio
async def test_check_compaction_threshold_triggers_auto(compact_session):
    """token 达到阈值时触发自动压缩。"""
    model = compact_session.agent.state.model
    model.provider = "test"
    model.id = "test-model"
    model.context_window = 100
    msg = MagicMock(
        stop_reason="stop",
        provider="test",
        model="test-model",
        usage=MagicMock(
            total_tokens=0, input=50, output=0, cache_read=50, cache_write=0
        ),
    )

    with (
        patch(
            "nova_harness.core.agent_session.controllers.compaction.calculate_context_tokens",
            return_value=100,
        ),
        patch(
            "nova_harness.core.agent_session.controllers.compaction.should_compact",
            return_value=True,
        ),
        patch.object(
            compact_session._compaction,
            "run_auto_compaction",
            AsyncMock(return_value=True),
        ),
    ):
        assert await compact_session._compaction.check_compaction(msg) is True
        compact_session._compaction.run_auto_compaction.assert_awaited_once_with(
            "threshold", will_retry=False
        )


@pytest.mark.asyncio
async def test_check_compaction_error_stop_reason_uses_estimate(compact_session):
    """stop_reason=error 时使用 estimate_context_tokens 估算。"""
    compact_session.agent.state.model.context_window = 100
    msg = MagicMock(stop_reason="error", usage=None)
    compact_session.agent.state.messages = [MagicMock(role="assistant")]

    with (
        patch(
            "nova_harness.core.agent_session.controllers.compaction.estimate_context_tokens",
            return_value=MagicMock(last_usage_index=0, tokens=90),
        ),
        patch(
            "nova_harness.core.agent_session.controllers.compaction.should_compact",
            return_value=True,
        ),
        patch.object(
            compact_session._compaction,
            "run_auto_compaction",
            AsyncMock(return_value=True),
        ),
    ):
        assert await compact_session._compaction.check_compaction(msg) is True


@pytest.mark.asyncio
async def test_check_compaction_no_usage_returns_false(compact_session):
    """正常 stop 消息没有 usage 时不压缩。"""
    compact_session.agent.state.model.context_window = 100
    msg = MagicMock(stop_reason="stop", provider="test", model="test-model", usage=None)
    assert await compact_session._compaction.check_compaction(msg) is False


# ---------------------------------------------------------------------------
# run_auto_compaction() 主路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_auto_compaction_no_model_returns_false(compact_session):
    """没有模型时发射结束事件并返回 False。"""
    compact_session.agent.state.model = None
    events = []
    compact_session.subscribe(events.append)
    assert (
        await compact_session._compaction.run_auto_compaction("threshold", False)
        is False
    )
    assert any(isinstance(e, CompactionEndEvent) for e in events)


@pytest.mark.asyncio
async def test_run_auto_compaction_no_api_key_returns_false(compact_session):
    """没有 API key 时返回 False。"""
    compact_session.model_registry.get_api_key = AsyncMock(return_value="")
    assert (
        await compact_session._compaction.run_auto_compaction("threshold", False)
        is False
    )


@pytest.mark.asyncio
async def test_run_auto_compaction_nothing_to_compact_returns_false(compact_session):
    """prepare_compaction 返回 None 时返回 False。"""
    compact_session.session_manager.get_branch.return_value = []
    with patch(
        "nova_harness.core.agent_session.controllers.compaction._compaction_module.prepare_compaction",
        return_value=None,
    ):
        assert (
            await compact_session._compaction.run_auto_compaction("threshold", False)
            is False
        )


@pytest.mark.asyncio
async def test_run_auto_compaction_success_threshold(compact_session):
    """自动压缩成功（threshold）应更新会话并发射事件。"""
    compact_session.session_manager.get_branch.return_value = []
    compact_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[MagicMock()]
    )
    compact_session.agent.has_queued_messages.return_value = False
    events = []
    compact_session.subscribe(events.append)

    with (
        patch(
            "nova_harness.core.agent_session.controllers.compaction._compaction_module.prepare_compaction",
            return_value=_make_preparation(),
        ),
        patch(
            "nova_harness.core.agent_session.controllers.compaction._compaction_module.compact",
            AsyncMock(return_value=_make_compact_result()),
        ),
    ):
        result = await compact_session._compaction.run_auto_compaction(
            "threshold", False
        )

    assert result is False  # agent.has_queued_messages 返回 False
    assert any(isinstance(e, SessionCompactEvent) for e in events)
    end_events = [e for e in events if isinstance(e, CompactionEndEvent)]
    assert end_events[-1].will_retry is False


@pytest.mark.asyncio
async def test_run_auto_compaction_success_overflow_will_retry(compact_session):
    """自动压缩成功（overflow）应移除最后的错误 assistant 消息。"""
    compact_session.session_manager.get_branch.return_value = []
    compact_session.session_manager.build_session_context.return_value = MagicMock(
        messages=[]
    )
    compact_session.agent.state.messages = [
        MagicMock(role="assistant", stop_reason="error")
    ]

    with (
        patch(
            "nova_harness.core.agent_session.controllers.compaction._compaction_module.prepare_compaction",
            return_value=_make_preparation(),
        ),
        patch(
            "nova_harness.core.agent_session.controllers.compaction._compaction_module.compact",
            AsyncMock(return_value=_make_compact_result()),
        ),
    ):
        result = await compact_session._compaction.run_auto_compaction("overflow", True)

    assert result is True
    assert compact_session.agent.state.messages == []


@pytest.mark.asyncio
async def test_run_auto_compaction_aborted(compact_session):
    """自动压缩过程中被取消应返回 False 并发射 aborted 事件。"""
    compact_session.session_manager.get_branch.return_value = []

    async def _cancel_and_return(*args, **kwargs):
        compact_session._auto_compaction_abort_controller.aborted = True
        return _make_compact_result()

    with (
        patch(
            "nova_harness.core.agent_session.controllers.compaction._compaction_module.prepare_compaction",
            return_value=_make_preparation(),
        ),
        patch(
            "nova_harness.core.agent_session.controllers.compaction._compaction_module.compact",
            AsyncMock(side_effect=_cancel_and_return),
        ),
    ):
        events = []
        compact_session.subscribe(events.append)
        assert (
            await compact_session._compaction.run_auto_compaction("threshold", False)
            is False
        )

    end_events = [e for e in events if isinstance(e, CompactionEndEvent)]
    assert end_events[-1].aborted is True
