"""retry_provider_request 测试（对齐 TS ``provider-retry.test.ts`` 要点）。"""

import asyncio

import httpx
import pytest
from nova_ai.api_impls._shared.retry import (
    _get_retry_delay_ms,
    _is_retryable_provider_error,
    retry_provider_request,
)
from nova_async import AbortController, AbortedError


class _FakeProviderError(Exception):
    """模拟 openai SDK 的 APIStatusError 形态（status_code + headers）。"""

    def __init__(self, status_code=None, headers=None, message="provider error"):
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers or {}


def test_is_retryable_cyclic_cause_chain_terminates():
    """循环 cause 链（respx SideEffectError 真实形状 A↔B）——环保护：
    找到传输错误且**终止**，不死循环。"""
    wrapper = Exception("sdk wrap")
    transport = httpx.ConnectError("refused")
    wrapper.__cause__ = transport
    transport.__cause__ = wrapper
    assert _is_retryable_provider_error(wrapper) is True


@pytest.mark.asyncio
async def test_success_first_attempt_no_retry():
    calls = []

    async def op():
        calls.append(1)
        return "ok"

    assert await retry_provider_request(op, {"max_retries": 3}) == "ok"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds():
    calls = []

    async def op():
        calls.append(1)
        if len(calls) == 1:
            raise _FakeProviderError(status_code=429, headers={"retry-after-ms": "1"})
        return "ok"

    result = await retry_provider_request(op, {"max_retries": 2})
    assert result == "ok"
    assert len(calls) == 2


@pytest.mark.parametrize("status", [408, 409, 429, 500, 503])
@pytest.mark.asyncio
async def test_retryable_status_codes(status):
    calls = []

    async def op():
        calls.append(1)
        if len(calls) == 1:
            raise _FakeProviderError(
                status_code=status, headers={"retry-after-ms": "1"}
            )
        return "ok"

    await retry_provider_request(op, {"max_retries": 1})
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
@pytest.mark.asyncio
async def test_non_retryable_status_codes_fail_fast(status):
    calls = []

    async def op():
        calls.append(1)
        raise _FakeProviderError(status_code=status)

    with pytest.raises(_FakeProviderError):
        await retry_provider_request(op, {"max_retries": 3})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_x_should_retry_header_overrides_status():
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

    await retry_provider_request(op, {"max_retries": 1})
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
        await retry_provider_request(op2, {"max_retries": 1})


@pytest.mark.asyncio
async def test_server_retry_delay_above_cap_fails_immediately():
    """服务端要求的等待超过 max_retry_delay_ms 时直接失败，不傻等——
    且原始 429 沿 ``__cause__`` 链可捞（归一分类保真，防重构丢链）。"""
    original = _FakeProviderError(status_code=429, headers={"retry-after-ms": "999999"})

    async def op():
        raise original

    with pytest.raises(RuntimeError, match="retry delay") as exc_info:
        await retry_provider_request(op, {"max_retries": 1, "max_retry_delay_ms": 1000})
    assert exc_info.value.__cause__ is original


@pytest.mark.asyncio
async def test_abort_during_backoff_raises_immediately():
    """退避睡眠期间 abort：立即抛 AbortedError，不等睡完。"""
    controller = AbortController()

    async def op():
        raise _FakeProviderError(status_code=429, headers={"retry-after-ms": "60000"})

    async def _abort_later():
        await asyncio.sleep(0.01)
        controller.abort()

    asyncio.ensure_future(_abort_later())
    with pytest.raises(AbortedError):
        await asyncio.wait_for(
            retry_provider_request(op, {"max_retries": 1, "signal": controller.signal}),
            timeout=5,
        )


@pytest.mark.asyncio
async def test_abort_during_backoff_does_not_reissue_request():
    """退避期间 abort：不再重发请求——即使重发会成功也必须按取消收场。"""
    controller = AbortController()
    calls = []

    async def op():
        calls.append(1)
        if len(calls) == 1:
            raise _FakeProviderError(
                status_code=429, headers={"retry-after-ms": "5000"}
            )
        return "ok"

    async def _abort_later():
        await asyncio.sleep(0.01)
        controller.abort()

    asyncio.ensure_future(_abort_later())
    with pytest.raises(AbortedError):
        await asyncio.wait_for(
            retry_provider_request(op, {"max_retries": 1, "signal": controller.signal}),
            timeout=5,
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_zero_max_retries_is_default():
    """默认不重试（对齐 TS maxRetries ?? 0——SDK 已被置 0，重试全归本层）。"""
    calls = []

    async def op():
        calls.append(1)
        raise _FakeProviderError(status_code=429, headers={"retry-after-ms": "1"})

    with pytest.raises(_FakeProviderError):
        await retry_provider_request(op)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_sdk_wrapped_connection_error_retried():
    """SDK 包装的连接错误（__cause__ 链上的传输错误）可重试——
    回归：openai/anthropic 的 APIConnectionError 不继承内建 ConnectionError，
    曾导致连接失败完全不走重试。"""
    import httpx

    calls = []

    async def op():
        calls.append(1)
        if len(calls) == 1:
            try:
                raise httpx.ConnectError("connection refused")
            except httpx.ConnectError as transport:
                raise _FakeProviderError() from transport
        return "ok"

    result = await retry_provider_request(op, {"max_retries": 1})
    assert result == "ok"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_sdk_error_retry_after_header_honored():
    """SDK 原生 429（APIStatusError，头在 ``.response.headers``）的
    retry-after-ms 被读取——经公开面表达：cap 1000ms < 指引 3000ms 时
    上限拒绝能抛出来，本身就证明头被读到（F1 回归锁；``headers_of``
    归一；读不到则落指数退避、cap 拒绝无从触发）。"""
    from openai import APIStatusError

    response = httpx.Response(
        429,
        headers={"retry-after-ms": "3000"},
        request=httpx.Request("POST", "http://x"),
    )

    async def op():
        raise APIStatusError("rate limited", response=response, body=None)

    with pytest.raises(RuntimeError, match="retry delay"):
        await retry_provider_request(op, {"max_retries": 1, "max_retry_delay_ms": 1000})


@pytest.mark.asyncio
async def test_sdk_error_x_should_retry_false_respected():
    """SDK 原生错误的 ``x-should-retry: false`` 头被尊重（F1 回归锁——
    此前读 ``error.headers`` 恒盲，500 会被误判可重试；公开面表达：
    op 只被调用一次）。"""
    from openai import APIStatusError

    response = httpx.Response(
        500,
        headers={"x-should-retry": "false"},
        request=httpx.Request("POST", "http://x"),
    )
    calls = 0

    async def op():
        nonlocal calls
        calls += 1
        raise APIStatusError("server error", response=response, body=None)

    with pytest.raises(APIStatusError):
        await retry_provider_request(op, {"max_retries": 3})
    assert calls == 1
