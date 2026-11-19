"""provider 终态与 driver 收尾的公开行为回归。"""

import asyncio

import pytest
from _helpers.agent import make_assistant_message
from nova_ai import AssistantMessageEventStream
from nova_async import AbortController
from nova_protocol import DoneEvent, ErrorEvent, StopReason

from nova_agent import Agent
from nova_agent.agent_loop.response import _finish_driver


def _terminal_stream(model, terminal: str, observed):
    """终值已发布、driver 仍在清理的受控流。"""
    stream = AssistantMessageEventStream()
    partial = make_assistant_message(model, [])

    async def drive():
        if terminal == "error":
            partial.stop_reason = StopReason.ERROR
            partial.error_message = "provider failed"
            stream.push(ErrorEvent(reason=StopReason.ERROR, error=partial))
        else:
            stream.push(DoneEvent(reason=StopReason.STOP, message=partial))
        try:
            await asyncio.sleep(0)
        finally:
            observed["cleanup"] = True

    observed["driver"] = stream.drive(drive())
    return stream


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["done", "error"])
async def test_terminal_completion_does_not_abort_driver(dummy_model, terminal):
    observed = {}

    def stream_fn(model, context, options):
        observed["signal"] = options.signal
        return _terminal_stream(model, terminal, observed)

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    await agent.prompt("go")

    driver = observed["driver"]
    await asyncio.gather(driver, return_exceptions=True)
    assert observed["cleanup"] is True
    assert observed["signal"].aborted is False
    assert driver.cancelled() is False


@pytest.mark.asyncio
async def test_driver_hard_cancel_cleanup_is_bounded():
    stream = AssistantMessageEventStream()
    first_cancel = asyncio.Event()

    async def drive():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            first_cancel.set()
            await asyncio.Event().wait()

    driver = stream.drive(drive())
    try:
        async with asyncio.timeout(0.5):
            await _finish_driver(stream, AbortController(), completed=True)
        assert first_cancel.is_set()
        assert not driver.done()
    finally:
        # 测试 driver 会吞第一次取消；收尾再补取消，不让测试自身泄漏任务。
        for _ in range(3):
            if driver.done():
                break
            driver.cancel()
            await asyncio.sleep(0)
        await asyncio.gather(driver, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_during_abort_handshake_cancels_driver():
    stream = AssistantMessageEventStream()
    finished = asyncio.Event()

    async def drive():
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    driver = stream.drive(drive())
    cleanup = asyncio.create_task(
        _finish_driver(stream, AbortController(), completed=False)
    )
    await asyncio.sleep(0)
    cleanup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cleanup
    await asyncio.gather(driver, return_exceptions=True)
    assert finished.is_set()
