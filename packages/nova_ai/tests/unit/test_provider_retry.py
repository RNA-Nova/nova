"""retry_provider_request 测试（对齐 TS ``provider-retry.test.ts`` 要点）。"""

import asyncio

import pytest

from nova_ai.api_impls._shared.retry import retry_provider_request
from nova_ai.signal import AbortController, AbortedError


class _FakeProviderError(Exception):
    """模拟 openai SDK 的 APIStatusError 形态（status_code + headers）。"""

    def __init__(self, status_code=None, headers=None, message="provider error"):
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers or {}


def _run(coro):
    return asyncio.run(coro)


def test_success_first_attempt_no_retry():
    calls = []

    async def op():
        calls.append(1)
        return "ok"

    assert _run(retry_provider_request(op, {"max_retries": 3})) == "ok"
    assert len(calls) == 1


def test_retries_on_429_then_succeeds():
    calls = []

    async def op():
        calls.append(1)
        if len(calls) == 1:
            raise _FakeProviderError(status_code=429, headers={"retry-after-ms": "1"})
        return "ok"

    result = _run(retry_provider_request(op, {"max_retries": 2}))
    assert result == "ok"
    assert len(calls) == 2


@pytest.mark.parametrize("status", [408, 409, 429, 500, 503])
def test_retryable_status_codes(status):
    calls = []

    async def op():
        calls.append(1)
        if len(calls) == 1:
            raise _FakeProviderError(
                status_code=status, headers={"retry-after-ms": "1"}
            )
        return "ok"

    _run(retry_provider_request(op, {"max_retries": 1}))
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_retryable_status_codes_fail_fast(status):
    calls = []

    async def op():
        calls.append(1)
        raise _FakeProviderError(status_code=status)

    with pytest.raises(_FakeProviderError):
        _run(retry_provider_request(op, {"max_retries": 3}))
    assert len(calls) == 1


def test_x_should_retry_header_overrides_status():
    """x-should-retry 头优先于状态码判定（对齐 SDK pinned 策略）。"""
    calls = []

    async def op():
        calls.append(1)
        if len(calls) == 1:
            # 400 本身不可重试，但头说 true
            raise _FakeProviderError(
                status_code=400, headers={"x-should-retry": "true"}
            )
        return "ok"

    _run(retry_provider_request(op, {"max_retries": 1}))
    assert len(calls) == 2

    calls.clear()

    async def op2():
        calls.append(1)
        if len(calls) == 1:
            # 500 本身可重试，但头说 false
            raise _FakeProviderError(
                status_code=500, headers={"x-should-retry": "false"}
            )
        return "ok"

    with pytest.raises(_FakeProviderError):
        _run(retry_provider_request(op2, {"max_retries": 1}))


def test_server_retry_delay_above_cap_fails_immediately():
    """服务端要求的等待超过 max_retry_delay_ms 时直接失败，不傻等。"""

    async def op():
        raise _FakeProviderError(status_code=429, headers={"retry-after-ms": "999999"})

    with pytest.raises(RuntimeError, match="retry delay"):
        _run(retry_provider_request(op, {"max_retries": 1, "max_retry_delay_ms": 1000}))


def test_abort_during_backoff_raises_immediately():
    """退避睡眠期间 abort：立即抛 AbortedError，不等睡完。"""
    controller = AbortController()

    async def op():
        raise _FakeProviderError(status_code=429, headers={"retry-after-ms": "60000"})

    async def main():
        async def _abort_later():
            await asyncio.sleep(0.01)
            controller.abort()

        asyncio.ensure_future(_abort_later())
        await retry_provider_request(
            op, {"max_retries": 1, "signal": controller.signal}
        )

    with pytest.raises(AbortedError):
        asyncio.run(asyncio.wait_for(main(), timeout=5))


def test_zero_max_retries_is_default():
    """默认不重试（对齐 TS maxRetries ?? 0——SDK 已被置 0，重试全归本层）。"""
    calls = []

    async def op():
        calls.append(1)
        raise _FakeProviderError(status_code=429, headers={"retry-after-ms": "1"})

    with pytest.raises(_FakeProviderError):
        _run(retry_provider_request(op))
    assert len(calls) == 1
