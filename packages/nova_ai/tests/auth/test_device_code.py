"""Device code 轮询测试。"""

import asyncio

import pytest

from nova_ai.auth.oauth.device_code import (
    DeviceCodePollOptions,
    DeviceCodePollResult,
    poll_oauth_device_code_flow,
)
from nova_ai.signal import AbortController


class _DummySignal:
    def __init__(self, aborted: bool = False):
        self.aborted = aborted

    def add_event_listener(self, *_args, **_kwargs) -> None:
        pass

    def remove_event_listener(self, *_args, **_kwargs) -> None:
        pass


@pytest.mark.asyncio
async def test_poll_returns_complete_value():
    async def _poll() -> DeviceCodePollResult[str]:
        return DeviceCodePollResult(status="complete", value="token")

    result = await poll_oauth_device_code_flow(
        DeviceCodePollOptions(poll=_poll, interval_seconds=0.01)
    )
    assert result == "token"


@pytest.mark.asyncio
async def test_poll_retries_pending_until_complete():
    calls = []

    async def _poll() -> DeviceCodePollResult[str]:
        calls.append(len(calls))
        if len(calls) < 3:
            return DeviceCodePollResult(status="pending")
        return DeviceCodePollResult(status="complete", value="token")

    result = await poll_oauth_device_code_flow(
        DeviceCodePollOptions(poll=_poll, interval_seconds=0.01)
    )
    assert result == "token"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_poll_slow_down_does_not_fail():
    calls = []

    async def _poll() -> DeviceCodePollResult[str]:
        calls.append(len(calls))
        if len(calls) == 1:
            return DeviceCodePollResult(status="slow_down")
        return DeviceCodePollResult(status="complete", value="token")

    result = await poll_oauth_device_code_flow(
        DeviceCodePollOptions(poll=_poll, interval_seconds=0.01)
    )
    assert result == "token"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_poll_failed_status_raises():
    async def _poll() -> DeviceCodePollResult[str]:
        return DeviceCodePollResult(status="failed", message="user denied")

    with pytest.raises(RuntimeError, match="user denied"):
        await poll_oauth_device_code_flow(
            DeviceCodePollOptions(poll=_poll, interval_seconds=0.01)
        )


@pytest.mark.asyncio
async def test_poll_times_out_when_always_pending():
    async def _poll() -> DeviceCodePollResult[str]:
        return DeviceCodePollResult(status="pending")

    with pytest.raises(TimeoutError, match="Device flow timed out"):
        await poll_oauth_device_code_flow(
            DeviceCodePollOptions(
                poll=_poll, interval_seconds=0.05, expires_in_seconds=0.1
            )
        )


@pytest.mark.asyncio
async def test_poll_cancels_when_signal_aborted():
    async def _poll() -> DeviceCodePollResult[str]:
        return DeviceCodePollResult(status="pending")

    with pytest.raises(asyncio.CancelledError, match="Login cancelled"):
        await poll_oauth_device_code_flow(
            DeviceCodePollOptions(
                poll=_poll,
                interval_seconds=0.01,
                signal=_DummySignal(aborted=True),
            )
        )


@pytest.mark.asyncio
async def test_poll_cancels_mid_run():
    signal = _DummySignal(aborted=False)
    calls = []

    async def _poll() -> DeviceCodePollResult[str]:
        calls.append(1)
        signal.aborted = True
        return DeviceCodePollResult(status="pending")

    with pytest.raises(asyncio.CancelledError, match="Login cancelled"):
        await poll_oauth_device_code_flow(
            DeviceCodePollOptions(poll=_poll, interval_seconds=0.01, signal=signal)
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_poll_waits_before_first_poll_when_requested():
    async def _poll() -> DeviceCodePollResult[str]:
        return DeviceCodePollResult(status="complete", value="token")

    start = asyncio.get_event_loop().time()
    result = await poll_oauth_device_code_flow(
        DeviceCodePollOptions(
            poll=_poll,
            interval_seconds=0.1,
            wait_before_first_poll=True,
        )
    )
    elapsed = asyncio.get_event_loop().time() - start
    assert result == "token"
    assert elapsed >= 0.08


@pytest.mark.asyncio
async def test_poll_slow_down_uses_server_interval():
    calls = []

    async def _poll() -> DeviceCodePollResult[str]:
        calls.append(len(calls))
        if len(calls) == 1:
            return DeviceCodePollResult(status="slow_down", interval_seconds=1.2)
        return DeviceCodePollResult(status="complete", value="token")

    start = asyncio.get_event_loop().time()
    result = await poll_oauth_device_code_flow(
        DeviceCodePollOptions(poll=_poll, interval_seconds=5)
    )
    elapsed = asyncio.get_event_loop().time() - start
    assert result == "token"
    # 如果没用 server 提供的 interval，会等 5s；用了 1.2s 就应该明显少于 5s。
    assert elapsed < 3.0


@pytest.mark.asyncio
async def test_poll_timeout_message_with_slow_down():
    async def _poll() -> DeviceCodePollResult[str]:
        return DeviceCodePollResult(status="slow_down")

    with pytest.raises(TimeoutError, match="clock drift"):
        await poll_oauth_device_code_flow(
            DeviceCodePollOptions(
                poll=_poll, interval_seconds=0.05, expires_in_seconds=0.1
            )
        )


@pytest.mark.asyncio
async def test_poll_cancels_with_nova_abort_controller():
    async def _poll() -> DeviceCodePollResult[str]:
        return DeviceCodePollResult(status="pending")

    controller = AbortController()
    controller.abort()

    with pytest.raises(asyncio.CancelledError, match="Login cancelled"):
        await poll_oauth_device_code_flow(
            DeviceCodePollOptions(
                poll=_poll,
                interval_seconds=5,
                signal=controller.signal,
            )
        )


@pytest.mark.asyncio
async def test_poll_cancels_mid_run_with_nova_abort_controller():
    controller = AbortController()
    calls = []

    async def _poll() -> DeviceCodePollResult[str]:
        calls.append(1)
        controller.abort()
        return DeviceCodePollResult(status="pending")

    with pytest.raises(asyncio.CancelledError, match="Login cancelled"):
        await poll_oauth_device_code_flow(
            DeviceCodePollOptions(
                poll=_poll,
                interval_seconds=5,
                signal=controller.signal,
            )
        )
    assert len(calls) == 1
