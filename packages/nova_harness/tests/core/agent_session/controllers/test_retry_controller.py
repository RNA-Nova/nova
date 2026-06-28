"""
RetryController 单元测试。

覆盖重试判定、退避等待、取消与设置开关。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nova_ai import AssistantMessage, Usage

from nova_harness.core.types.events import AutoRetryEndEvent, AutoRetryStartEvent


@pytest.fixture
def retry_session(make_agent_session):
    """构造一个启用自动重试的 session。"""
    sess = make_agent_session()
    sess.settings_manager.get_retry_settings.return_value = MagicMock(
        enabled=True,
        max_retries=3,
        base_delay_ms=1,
        max_delay_ms=100,
    )
    sess.agent.state.model.context_window = 100000
    return sess


def test_is_retrying_and_attempt(retry_session):
    """is_retrying 与 attempt 应读取内部状态。"""
    assert retry_session._retry.is_retrying is False
    assert retry_session._retry.attempt == 0
    retry_session._retry_attempt = 2
    assert retry_session._retry.attempt == 2


def test_will_retry_after_agent_end_disabled(make_agent_session):
    """重试禁用时返回 False。"""
    sess = make_agent_session()
    sess.settings_manager.get_retry_settings.return_value = MagicMock(enabled=False)
    event = SimpleNamespace(messages=[])
    assert sess._retry.will_retry_after_agent_end(event) is False


def test_will_retry_after_agent_end_max_retries(retry_session):
    """已达最大重试次数时返回 False。"""
    retry_session._retry_attempt = 3
    event = SimpleNamespace(
        messages=[AssistantMessage(role="assistant", stop_reason="error")]
    )
    assert retry_session._retry.will_retry_after_agent_end(event) is False


def test_will_retry_after_agent_end_no_assistant(retry_session):
    """没有 assistant 消息时返回 False。"""
    event = SimpleNamespace(messages=[SimpleNamespace(role="user")])
    assert retry_session._retry.will_retry_after_agent_end(event) is False


def test_will_retry_after_agent_end_retryable(retry_session):
    """最后一条 assistant 消息可重试时返回 True。"""
    msg = AssistantMessage(
        role="assistant",
        stop_reason="error",
        error_message="rate limit",
        usage=Usage(),
    )
    event = SimpleNamespace(messages=[msg])
    assert retry_session._retry.will_retry_after_agent_end(event) is True


def test_is_retryable_error_requires_error_stop_reason(retry_session):
    """stop_reason 不是 error 时不可重试。"""
    msg = AssistantMessage(role="assistant", stop_reason="stop", usage=Usage())
    assert retry_session._retry.is_retryable_error(msg) is False


def test_is_retryable_error_requires_error_message(retry_session):
    """没有 error_message 时不可重试。"""
    msg = AssistantMessage(role="assistant", stop_reason="error", usage=Usage())
    assert retry_session._retry.is_retryable_error(msg) is False


def test_is_retryable_error_skips_context_overflow(retry_session):
    """上下文溢出错误不可重试。"""
    msg = AssistantMessage(
        role="assistant",
        stop_reason="error",
        error_message="context length exceeded",
        usage=Usage(input=80, cache_read=30, output=5),
    )
    assert retry_session._retry.is_retryable_error(msg) is False


def test_is_retryable_error_non_retryable_keywords(retry_session):
    """计费/配额类错误不可重试。"""
    msg = AssistantMessage(
        role="assistant",
        stop_reason="error",
        error_message="insufficient_quota",
        usage=Usage(),
    )
    assert retry_session._retry.is_retryable_error(msg) is False


def test_is_retryable_error_retryable_keywords(retry_session):
    """可重试关键字应返回 True。"""
    msg = AssistantMessage(
        role="assistant",
        stop_reason="error",
        error_message="provider returned error 503",
        usage=Usage(),
    )
    assert retry_session._retry.is_retryable_error(msg) is True


@pytest.mark.asyncio
async def test_prepare_retry_disabled(make_agent_session):
    """重试禁用时 prepare_retry 返回 False。"""
    sess = make_agent_session()
    sess.settings_manager.get_retry_settings.return_value = MagicMock(enabled=False)
    msg = AssistantMessage(role="assistant", stop_reason="error", error_message="x")
    assert await sess._retry.prepare_retry(msg) is False


@pytest.mark.asyncio
async def test_prepare_retry_success(retry_session):
    """prepare_retry 应递增 attempt、发射事件并返回 True。"""
    msg = AssistantMessage(
        role="assistant",
        stop_reason="error",
        error_message="timeout",
        usage=Usage(),
    )
    retry_session.agent.state.messages = [msg]
    events = []
    retry_session.subscribe(events.append)

    # 模拟退避等待超时，避免创建未 await 的 Event.wait 协程
    with patch("asyncio.Event.wait", AsyncMock(side_effect=asyncio.TimeoutError)):
        result = await retry_session._retry.prepare_retry(msg)

    assert result is True
    assert retry_session._retry_attempt == 1
    assert retry_session.agent.state.messages == []
    start_events = [e for e in events if isinstance(e, AutoRetryStartEvent)]
    assert len(start_events) == 1
    assert start_events[0].attempt == 1


@pytest.mark.asyncio
async def test_prepare_retry_exceeds_max_retries(retry_session):
    """超过最大重试次数时返回 False 且不递增计数。"""
    retry_session._retry_attempt = 3
    msg = AssistantMessage(role="assistant", stop_reason="error", error_message="x")
    result = await retry_session._retry.prepare_retry(msg)
    assert result is False
    assert retry_session._retry_attempt == 3


@pytest.mark.asyncio
async def test_prepare_retry_aborted(retry_session):
    """等待期间被取消应返回 False 并发射 AutoRetryEndEvent。"""
    msg = AssistantMessage(role="assistant", stop_reason="error", error_message="x")
    events = []
    retry_session.subscribe(events.append)

    # 让 abort event 立即返回，模拟 abort event 被触发
    with patch("asyncio.Event.wait", AsyncMock(return_value=None)):
        result = await retry_session._retry.prepare_retry(msg)

    assert result is False
    end_events = [e for e in events if isinstance(e, AutoRetryEndEvent)]
    assert len(end_events) == 1
    assert end_events[0].success is False
    assert "cancelled" in end_events[0].final_error


def test_abort_retry_sets_event(retry_session):
    """abort_retry 应设置 abort event。"""
    event = asyncio.Event()
    retry_session._retry_abort_event = event
    retry_session._retry.abort_retry()
    assert event.is_set() is True


def test_set_auto_retry_enabled(retry_session):
    """set_auto_retry_enabled 应委托给 settings_manager。"""
    retry_session._retry.set_auto_retry_enabled(False)
    retry_session.settings_manager.set_retry_enabled.assert_called_once_with(False)
