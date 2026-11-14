"""公平读写门及其在工具批中的并发、取消与临界区契约。"""

import asyncio
from typing import Any, List

import pytest
from _helpers.agent import (
    EchoTool,
    SlowTool,
    final_stream,
    multi_tool_call_stream,
    text_stream,
)
from _helpers.messages import text_of, tool_result_message
from nova_ai import SimpleStreamOptions
from nova_async import AbortController
from nova_protocol import AgentToolResult, TextContent, ToolResultMessage, UserMessage

from nova_agent import Agent, AgentContext, AgentLoopConfig
from nova_agent.agent_loop import run_agent_loop
from nova_agent.agent_loop.execution_gate import ToolExecutionGate


@pytest.mark.asyncio
async def test_gate_readers_overlap() -> None:
    gate = ToolExecutionGate()
    await gate.acquire(False)
    await gate.acquire(False)
    await gate.release(False)
    await gate.release(False)


@pytest.mark.asyncio
async def test_gate_writer_waits_for_readers() -> None:
    gate = ToolExecutionGate()
    events: List[str] = []
    waiting = asyncio.Event()
    await gate.acquire(False)

    async def writer() -> None:
        waiting.set()
        await gate.acquire(True)
        events.append("writer-in")
        await gate.release(True)

    task = asyncio.create_task(writer())
    await waiting.wait()
    assert events == []
    await gate.release(False)
    await task
    assert events == ["writer-in"]


@pytest.mark.asyncio
async def test_gate_fifo_writer_blocks_later_readers() -> None:
    gate = ToolExecutionGate()
    order: List[str] = []
    writer_waiting = asyncio.Event()
    reader_waiting = asyncio.Event()
    await gate.acquire(False)

    async def reader() -> None:
        reader_waiting.set()
        await gate.acquire(False)
        order.append("r2")
        await gate.release(False)

    async def writer() -> None:
        writer_waiting.set()
        await gate.acquire(True)
        order.append("w1")
        await gate.release(True)

    writer_task = asyncio.create_task(writer())
    await writer_waiting.wait()
    reader_task = asyncio.create_task(reader())
    await reader_waiting.wait()
    await gate.release(False)
    await asyncio.gather(writer_task, reader_task)
    assert order == ["w1", "r2"]


@pytest.mark.asyncio
async def test_gate_cancelled_waiter_self_removes_and_never_runs() -> None:
    gate = ToolExecutionGate()
    waiting = asyncio.Event()
    await gate.acquire(True)

    async def acquire_reader() -> None:
        waiting.set()
        await gate.acquire(False)

    waiter = asyncio.create_task(acquire_reader())
    await waiting.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    await gate.release(True)
    async with asyncio.timeout(1):
        await gate.acquire(True)
    await gate.release(True)


@pytest.mark.asyncio
async def test_mixed_batch_parallel_overlap_sequential_exclusive(dummy_model) -> None:
    """两个读门工具同时停在场内，写门工具必须等它们全部离开。"""
    events: List[str] = []
    both_parallel_started = asyncio.Event()
    release_parallel = asyncio.Event()

    class ParallelTool(EchoTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            name = params["message"]
            events.append(f"{name}:start")
            if events.count("p1:start") + events.count("p2:start") == 2:
                both_parallel_started.set()
            await release_parallel.wait()
            events.append(f"{name}:end")
            return AgentToolResult(
                content=[TextContent(text=f"echo: {name}")], details={}
            )

    class SequentialTool(SlowTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            events.append("sequential")
            return AgentToolResult(
                content=[TextContent(text=f"slow-{params['value']}")], details={}
            )

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return multi_tool_call_stream(
                model,
                [
                    ("echo", {"message": "p1"}),
                    ("echo", {"message": "p2"}),
                    ("slow", {"value": "s1"}),
                ],
            )
        return text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn, tool_execution="parallel")
    agent.set_model(dummy_model)
    agent.set_tools([ParallelTool(), SequentialTool()])
    run = agent.start_prompt("go")
    await both_parallel_started.wait()
    assert events == ["p1:start", "p2:start"]

    release_parallel.set()
    await run
    assert events[-1] == "sequential"
    assert set(events[2:5]) == {"p1:end", "p2:end", "sequential"}
    results = [
        message
        for message in agent.state.messages
        if isinstance(message, ToolResultMessage)
    ]
    assert [text_of(result) for result in results] == [
        "echo: p1",
        "echo: p2",
        "slow-s1",
    ]


@pytest.mark.asyncio
async def test_two_sequential_tools_fifo_order(dummy_model) -> None:
    events: List[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class SequentialTool(SlowTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            name = params["value"]
            events.append(f"{name}:start")
            if name == "first":
                first_started.set()
                await release_first.wait()
            events.append(f"{name}:end")
            return AgentToolResult(content=[TextContent(text=name)], details={})

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return multi_tool_call_stream(
                model,
                [("slow", {"value": "first"}), ("slow", {"value": "second"})],
            )
        return text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn, tool_execution="parallel")
    agent.set_model(dummy_model)
    agent.set_tools([SequentialTool()])
    run = agent.start_prompt("go")
    await first_started.wait()
    assert events == ["first:start"]

    release_first.set()
    await run
    assert events == ["first:start", "first:end", "second:start", "second:end"]


@pytest.mark.asyncio
async def test_abort_while_waiting_on_gate_never_executes(dummy_model) -> None:
    ran: List[str] = []
    blocking_started = asyncio.Event()
    sequential_pending = asyncio.Event()

    class GateSlowTool(SlowTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            ran.append(params["value"])
            return AgentToolResult(content=[TextContent(text="x")], details={})

    class BlockingEchoTool(EchoTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            assert signal is not None
            blocking_started.set()
            await signal.wait()
            return AgentToolResult(
                content=[TextContent(text="echo-aborted")], details={}
            )

    controller = AbortController()

    async def emit(event: Any) -> None:
        if event.type == "tool_execution_start" and event.tool_name == "slow":
            sequential_pending.set()

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return multi_tool_call_stream(
                model,
                [("echo", {"message": "block"}), ("slow", {"value": "gated"})],
            )
        return final_stream(model, "aborted", stop_reason="aborted")

    run = asyncio.create_task(
        run_agent_loop(
            [UserMessage(role="user", content="go")],
            AgentContext(messages=[], tools=[BlockingEchoTool(), GateSlowTool()]),
            AgentLoopConfig(stream_options=SimpleStreamOptions(), model=dummy_model),
            emit,
            signal=controller.signal,
            stream_fn=stream_fn,
        )
    )
    await blocking_started.wait()
    await sequential_pending.wait()
    controller.abort()
    messages = await run

    assert ran == []
    gated_result = tool_result_message(
        next(
            message
            for message in messages
            if isinstance(message, ToolResultMessage) and message.tool_name == "slow"
        )
    )
    assert gated_result.is_error is True
    assert "aborted" in text_of(gated_result)


def _gated_tools(started, sequential_started=None):
    class ParallelTool(EchoTool):
        name: str = "parallel"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            return AgentToolResult(content=[TextContent(text="parallel")], details={})

    class SequentialTool(SlowTool):
        name: str = "sequential"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            if sequential_started is not None:
                sequential_started.set()
            started.append("sequential")
            return AgentToolResult(content=[TextContent(text="sequential")], details={})

    return [ParallelTool(), SequentialTool()]


def _tool_then_text_stream(tool_names):
    calls = 0

    def stream(model, context, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return multi_tool_call_stream(model, tool_names)
        return text_stream(model, "done")

    return stream


@pytest.mark.asyncio
async def test_after_hook_stays_inside_execution_gate(dummy_model):
    started = []
    after_started = asyncio.Event()
    release_after = asyncio.Event()

    async def after(ctx, _signal):
        if ctx.tool_call.name == "parallel":
            after_started.set()
            await release_after.wait()

    agent = Agent(
        stream_fn=_tool_then_text_stream([("parallel", {}), ("sequential", {})]),
        after_tool_call=after,
    )
    agent.set_model(dummy_model)
    agent.set_tools(_gated_tools(started))
    prompt_task = agent.start_prompt("go")
    await after_started.wait()
    assert started == []
    release_after.set()
    await prompt_task
    assert started == ["sequential"]


@pytest.mark.asyncio
async def test_end_listener_is_outside_execution_gate(dummy_model):
    started = []
    sequential_started = asyncio.Event()
    end_started = asyncio.Event()
    release_end = asyncio.Event()

    async def on_end(event, _signal):
        if event.type == "tool_execution_end" and event.tool_name == "parallel":
            end_started.set()
            await release_end.wait()

    agent = Agent(
        stream_fn=_tool_then_text_stream([("parallel", {}), ("sequential", {})])
    )
    agent.set_model(dummy_model)
    agent.set_tools(_gated_tools(started, sequential_started))
    agent.subscribe(on_end)
    prompt_task = agent.start_prompt("go")
    await end_started.wait()
    await sequential_started.wait()
    assert started == ["sequential"]
    release_end.set()
    await prompt_task
