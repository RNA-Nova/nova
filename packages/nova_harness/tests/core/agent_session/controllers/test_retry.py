"""RetryController 测试——归一 kind 驱动的重试判定 + 服务端退避优先。

锁定诊断链的消费端纪律（diagnostics-modeling 波次 3）：
- 可重试性只读 ``error_kind``（``RETRYABLE_ERROR_KINDS`` 成员），不读文案；
- 溢出（kind / 用量超窗）归压缩不重试；
- 服务端 ``retry_after_ms`` 指引优先于本地指数退避（封顶 provider 上限）。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from nova_protocol import AssistantMessage, Model, ModelCost, NovaErrorKind, Usage

from nova_harness.core.agent_session.controllers.retry import RetryController


def _model() -> Model:
    return Model(
        id="m",
        name="m",
        api="openai-completions",
        provider="test",
        base_url="",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0),
        context_window=1000000,
        max_tokens=4096,
    )


def _assistant(**overrides) -> AssistantMessage:
    base = dict(
        role="assistant",
        content=[{"type": "text", "text": "x"}],
        api="openai-completions",
        provider="test",
        model="m",
        usage=Usage(),
        stop_reason="error",
        error_message="boom",
    )
    return AssistantMessage.model_validate(base | overrides)


def _session(
    *,
    base_delay_ms: int = 1000,
    max_retry_delay_ms: int = 60000,
    messages: list | None = None,
) -> MagicMock:
    session = MagicMock()
    session.model = _model()
    session._retry_attempt = 0
    session._retry_abort_event = None
    session.settings_manager.get_retry_settings.return_value = SimpleNamespace(
        enabled=True,
        max_retries=3,
        base_delay_ms=base_delay_ms,
        provider=SimpleNamespace(max_retry_delay_ms=max_retry_delay_ms),
    )
    session.agent.state.messages = list(messages or [])
    return session


class TestIsRetryableError:
    """读归一 kind——两串文案清单已删除（铁律二的消费端兑现）。"""

    @pytest.mark.parametrize(
        "kind",
        [
            NovaErrorKind.RATE_LIMITED,
            NovaErrorKind.OVERLOADED,
            NovaErrorKind.UPSTREAM_ERROR,
            NovaErrorKind.REQUEST_TIMEOUT,
            NovaErrorKind.HTTP_CONNECTION_FAILED,
            NovaErrorKind.STREAM_DISCONNECTED,
        ],
    )
    def test_retryable_kinds(self, kind):
        controller = RetryController(_session())
        assert controller.is_retryable_error(_assistant(error_kind=kind)) is True

    @pytest.mark.parametrize(
        "kind",
        [
            NovaErrorKind.CONTEXT_OVERFLOW,  # 溢出归压缩
            NovaErrorKind.USAGE_LIMIT_EXCEEDED,  # 额度用尽不重试
            NovaErrorKind.UNAUTHORIZED,
            NovaErrorKind.LOGIN_REQUIRED,
            NovaErrorKind.INTERNAL,  # 真 bug 不反复重试
            NovaErrorKind.BAD_REQUEST,
            NovaErrorKind.MODEL_NOT_FOUND,
        ],
    )
    def test_non_retryable_kinds(self, kind):
        controller = RetryController(_session())
        assert controller.is_retryable_error(_assistant(error_kind=kind)) is False

    def test_unclassified_not_retried(self):
        """未分类（无 kind——旧数据/未归一）不重试。"""
        controller = RetryController(_session())
        assert controller.is_retryable_error(_assistant(error_kind=None)) is False

    def test_non_error_stop_not_retried(self):
        controller = RetryController(_session())
        assert (
            controller.is_retryable_error(
                _assistant(stop_reason="stop", error_kind=None, error_message=None)
            )
            is False
        )

    def test_usage_overflow_not_retried(self):
        """用量超窗的错误响应（无 kind 分类时）仍归压缩不重试。"""
        session = _session()
        session.model = _model()
        session.model = session.model.model_copy(update={"context_window": 10})
        controller = RetryController(session)
        msg = _assistant(
            error_kind=NovaErrorKind.UPSTREAM_ERROR,
            usage=Usage(input=100, output=0),
        )
        assert controller.is_retryable_error(msg) is False


class TestPrepareRetryDelay:
    """服务端退避指引优先（codex retry_delay().unwrap_or(backoff) 对位）。"""

    @pytest.mark.asyncio
    async def test_server_retry_after_wins_over_backoff(self):
        """retry_after_ms 存在时用服务端值（封顶 provider 上限），不走指数。"""
        msg = _assistant(error_kind=NovaErrorKind.RATE_LIMITED, retry_after_ms=5)
        session = _session(
            base_delay_ms=100000, max_retry_delay_ms=60000, messages=[msg]
        )
        controller = RetryController(session)
        ok = await controller.prepare_retry(msg)
        assert ok is True
        emitted = [c.args[0] for c in session._emit.call_args_list if c.args]
        start = next(e for e in emitted if type(e).__name__ == "AutoRetryStartEvent")
        assert start.delay_ms == 5

    @pytest.mark.asyncio
    async def test_backoff_when_no_server_guidance(self):
        """无服务端指引：本地指数退避（attempt 2 → base*2）。"""
        msg = _assistant(error_kind=NovaErrorKind.RATE_LIMITED)
        session = _session(base_delay_ms=1, messages=[msg])
        session._retry_attempt = 1  # prepare 内先 +1 → 实际 attempt 2
        controller = RetryController(session)
        ok = await controller.prepare_retry(msg)
        assert ok is True
        emitted = [c.args[0] for c in session._emit.call_args_list if c.args]
        start = next(e for e in emitted if type(e).__name__ == "AutoRetryStartEvent")
        assert start.delay_ms == 2

    @pytest.mark.asyncio
    async def test_server_delay_capped_by_provider_max(self):
        """服务端要求超过 provider 上限时按上限封顶。"""
        msg = _assistant(error_kind=NovaErrorKind.RATE_LIMITED, retry_after_ms=999999)
        session = _session(max_retry_delay_ms=1, messages=[msg])
        controller = RetryController(session)
        ok = await controller.prepare_retry(msg)
        assert ok is True
        emitted = [c.args[0] for c in session._emit.call_args_list if c.args]
        start = next(e for e in emitted if type(e).__name__ == "AutoRetryStartEvent")
        assert start.delay_ms == 1
