"""Agent run 的取消、代际清理与子任务排水回归。"""

import asyncio

import pytest
from _helpers.agent import make_assistant_message, multi_tool_call_stream, text_stream
from _helpers.messages import assistant_message, text_of
from nova_ai import AssistantMessageEventStream
from nova_async import AbortedError
from nova_protocol import (
    AgentToolResult,
    ErrorEvent,
    NovaErrorKind,
    StartEvent,
    StopReason,
    TextContent,
)

from nova_agent import Agent, AgentTool


def _pending_stream(model, started, finished, holder, options):
    """首块后永久等待的 provider 流；取消时必须经 finally 落定。"""
    stream = AssistantMessageEventStream()
    stream.push(StartEvent(partial=make_assistant_message(model, [])))
    holder["signal"] = options.signal

    async def drive():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    holder["driver"] = stream.drive(drive())
    return stream


@pytest.mark.asyncio
async def test_run_failure_is_normalized_and_redacted(dummy_model):
    def broken_stream(model, context, options):
        raise RuntimeError("request failed: Bearer abcdefghijklmnop")

    agent = Agent(stream_fn=broken_stream)
    agent.set_model(dummy_model)
    await agent.prompt("hello")

    result = assistant_message(agent.state.messages[-1])
    assert result.error_kind is NovaErrorKind.INTERNAL
    assert "abcdefghijklmnop" not in (result.error_message or "")
    assert "abcdefghijklmnop" not in (agent.state.error_message or "")


@pytest.mark.asyncio
async def test_external_cancel_abandons_and_joins_provider(dummy_model):
    started = asyncio.Event()
    finished = asyncio.Event()
    holder = {}

    def stream_fn(model, context, options):
        return _pending_stream(model, started, finished, holder, options)

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    prompt_task = asyncio.create_task(agent.prompt("hello"))
    await started.wait()
    prompt_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await prompt_task

    driver = holder["driver"]
    await asyncio.gather(driver, return_exceptions=True)
    assert finished.is_set()
    assert holder["signal"].aborted
    assert not agent.state.is_streaming


@pytest.mark.asyncio
async def test_listener_error_abandons_and_joins_provider(dummy_model):
    started = asyncio.Event()
    finished = asyncio.Event()
    holder = {}

    def stream_fn(model, context, options):
        return _pending_stream(model, started, finished, holder, options)

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    failed = False

    def fail_once(event, _signal):
        nonlocal failed
        if (
            event.type == "message_start"
            and event.message.role == "assistant"
            and not failed
        ):
            failed = True
            raise RuntimeError("listener failed")

    agent.subscribe(fail_once)
    await agent.prompt("hello")

    driver = holder["driver"]
    await asyncio.gather(driver, return_exceptions=True)
    assert finished.is_set()
    assert holder["signal"].aborted
    assert (
        assistant_message(agent.state.messages[-1]).error_kind is NovaErrorKind.INTERNAL
    )


@pytest.mark.asyncio
async def test_wait_for_idle_tracks_active_run_until_completion(dummy_model):
    started = asyncio.Event()
    release = asyncio.Event()

    async def stream_fn(model, context, options):
        started.set()
        await release.wait()
        return text_stream(model, "done")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    run = agent.start_prompt("one")
    await started.wait()
    waiter = asyncio.create_task(agent.wait_for_idle())
    await asyncio.sleep(0)
    assert not waiter.done()

    release.set()
    await waiter
    await run
    assert text_of(assistant_message(agent.state.messages[-1])) == "done"
    assert agent.signal is None


@pytest.mark.asyncio
async def test_public_streaming_flag_cannot_bypass_active_guard(dummy_model):
    started = asyncio.Event()
    finished = asyncio.Event()
    holder = {}

    def stream_fn(model, context, options):
        return _pending_stream(model, started, finished, holder, options)

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    prompt_task = asyncio.create_task(agent.prompt("hello"))
    await started.wait()
    agent.state.is_streaming = False
    with pytest.raises(RuntimeError, match="already processing"):
        agent.reset()

    prompt_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await prompt_task
    driver = holder["driver"]
    await asyncio.gather(driver, return_exceptions=True)
    assert finished.is_set()


@pytest.mark.asyncio
async def test_parallel_tool_failure_cancels_siblings_before_agent_end(dummy_model):
    started = asyncio.Event()
    finished = asyncio.Event()

    class FastTool(AgentTool):
        name: str = "fast"
        label: str = "fast"
        description: str = "fast"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            return AgentToolResult(content=[TextContent(text="fast")], details={})

    class SlowTool(AgentTool):
        name: str = "slow"
        label: str = "slow"
        description: str = "slow"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            started.set()
            try:
                await asyncio.Event().wait()
                return AgentToolResult(content=[TextContent(text="slow")], details={})
            finally:
                finished.set()

    agent = Agent(
        stream_fn=lambda model, context, options: multi_tool_call_stream(
            model, [("fast", {}), ("slow", {})]
        )
    )
    agent.set_model(dummy_model)
    agent.set_tools([FastTool(), SlowTool()])

    def fail_fast(event, _signal):
        if event.type == "tool_execution_end" and event.tool_name == "fast":
            raise RuntimeError("listener failed")

    agent.subscribe(fail_fast)
    await agent.prompt("go")

    assert started.is_set()
    assert finished.is_set()
    assert not agent.state.is_streaming


@pytest.mark.asyncio
async def test_start_prompt_registers_before_returning_task(dummy_model):
    started = asyncio.Event()
    finished = asyncio.Event()
    holder = {}

    def stream_fn(model, context, options):
        stream = AssistantMessageEventStream()
        partial = make_assistant_message(model, [])
        stream.push(StartEvent(partial=partial))

        async def drive():
            started.set()
            try:
                await options.signal.wait()
                partial.stop_reason = StopReason.ABORTED
                partial.error_message = "aborted"
                stream.push(ErrorEvent(reason=StopReason.ABORTED, error=partial))
            finally:
                finished.set()

        holder["driver"] = stream.drive(drive())
        return stream

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    task = agent.start_prompt("hello")
    assert agent.state.is_streaming
    assert agent.signal is not None
    await started.wait()

    agent.abort()
    await task
    assert assistant_message(agent.state.messages[-1]).stop_reason is StopReason.ABORTED
    assert finished.is_set()
    assert not agent.state.is_streaming
    assert agent.signal is None

    agent.stream_fn = lambda model, context, options: text_stream(model, "next")
    await agent.prompt("next")
    assert text_of(assistant_message(agent.state.messages[-1])) == "next"


@pytest.mark.asyncio
async def test_abort_timeout_forces_cancellation_and_warns(dummy_model, caplog):
    started = asyncio.Event()
    finished = asyncio.Event()
    holder = {}

    def stream_fn(model, context, options):
        stream = AssistantMessageEventStream()
        stream.push(StartEvent(partial=make_assistant_message(model, [])))

        async def drive():
            started.set()
            try:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    # 第一次硬取消也不配合，用于验证超时有界收尾和告警。
                    await asyncio.Event().wait()
            finally:
                finished.set()

        holder["driver"] = stream.drive(drive())
        return stream

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    task = agent.start_prompt("hello")
    await started.wait()
    with caplog.at_level("WARNING"):
        agent.abort()
        async with asyncio.timeout(1.0):
            await task

    assert assistant_message(agent.state.messages[-1]).stop_reason is StopReason.ABORTED
    assert any("forced cancellation" in record.message for record in caplog.records)
    driver = holder["driver"]
    if not driver.done():
        driver.cancel()
    await asyncio.gather(driver, return_exceptions=True)
    assert finished.is_set()
    assert agent.signal is None

    agent.stream_fn = lambda model, context, options: text_stream(model, "next")
    await agent.prompt("next")
    assert text_of(assistant_message(agent.state.messages[-1])) == "next"


@pytest.mark.asyncio
async def test_start_prompt_normal_completion_allows_next_generation(dummy_model):
    calls = 0

    def stream_fn(model, context, options):
        nonlocal calls
        calls += 1
        return text_stream(model, f"reply-{calls}")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)

    await agent.start_prompt("first")
    assert not agent.state.is_streaming
    assert agent.signal is None

    await agent.prompt("second")
    assert calls == 2
    assert [message.role for message in agent.state.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert text_of(assistant_message(agent.state.messages[-1])) == "reply-2"


@pytest.mark.asyncio
async def test_start_prompt_rejects_second_active_run(dummy_model):
    started = asyncio.Event()
    finished = asyncio.Event()
    holder = {}

    def stream_fn(model, context, options):
        return _pending_stream(model, started, finished, holder, options)

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    task = agent.start_prompt("first")
    await started.wait()

    with pytest.raises(RuntimeError, match="already processing"):
        agent.start_prompt("second")

    agent.abort()
    await task
    await asyncio.gather(holder["driver"], return_exceptions=True)
    assert finished.is_set()


def test_start_prompt_without_model_raises_synchronously():
    agent = Agent(stream_fn=lambda model, context, options: text_stream(model, "ok"))
    with pytest.raises(RuntimeError, match="No model configured"):
        agent.start_prompt("hello")


@pytest.mark.asyncio
async def test_start_prompt_external_cancel_allows_next_generation(dummy_model):
    started = asyncio.Event()
    finished = asyncio.Event()
    holder = {}

    def stream_fn(model, context, options):
        return _pending_stream(model, started, finished, holder, options)

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(dummy_model)
    task = agent.start_prompt("first")
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.gather(holder["driver"], return_exceptions=True)
    assert finished.is_set()
    assert holder["signal"].aborted
    assert not agent.state.is_streaming
    assert agent.signal is None

    agent.stream_fn = lambda model, context, options: text_stream(model, "next")
    await agent.prompt("second")
    assert text_of(assistant_message(agent.state.messages[-1])) == "next"


@pytest.mark.asyncio
async def test_reset_during_run_raises_and_abort_allows_reuse(dummy_model):
    started = asyncio.Event()

    class HangingTool(AgentTool):
        name: str = "hang"
        description: str = "hang"
        label: str = "Hang"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            started.set()
            assert signal is not None
            await signal.wait()
            raise AbortedError("Operation aborted")

    calls = 0

    def stream(model, context, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return multi_tool_call_stream(model, [("hang", {})])
        return text_stream(model, "done")

    agent = Agent(stream_fn=stream)
    agent.set_model(dummy_model)
    agent.set_tools([HangingTool()])
    run = agent.start_prompt("run")
    await started.wait()

    with pytest.raises(RuntimeError, match="already processing"):
        agent.reset()

    agent.abort()
    await run
    agent.set_tools([])
    await agent.prompt("next")
    assert text_of(assistant_message(agent.state.messages[-1])) == "done"
