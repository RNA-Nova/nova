"""工具执行、单次调用、终止矩阵与结果元数据契约。"""

import asyncio

import pytest
from _helpers.agent import (
    EchoTool,
    TerminateTool,
    make_assistant_message,
    multi_tool_call_stream,
    text_stream,
    tool_call_stream,
    tool_call_then_text_stream,
)
from _helpers.messages import tool_result_message
from nova_protocol import (
    AbortedError,
    AgentToolResult,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
)

from nova_agent import (
    AfterToolCallResult,
    Agent,
    AgentContext,
    AgentTool,
    AgentToolCallOutcome,
    BeforeToolCallResult,
    RunToolCallOptions,
    run_tool_call,
)


def _only_tool_result(agent: Agent) -> ToolResultMessage:
    return tool_result_message(
        next(
            message for message in agent.state.messages if message.role == "toolResult"
        )
    )


@pytest.mark.asyncio
async def test_non_dict_details_passthrough(dummy_model):
    """任意 JSON details 原样进入 toolResult，不被改写为错误。"""

    class ListDetailsTool(AgentTool):
        name: str = "list_details"
        description: str = "returns list details"
        label: str = "ListDetails"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            return AgentToolResult(content=[TextContent(text="ok")], details=["a", "b"])

    agent = Agent(
        stream_fn=tool_call_then_text_stream("list_details", {}),
    )
    agent.set_model(dummy_model)
    agent.set_tools([ListDetailsTool()])
    await agent.prompt("run")

    result = _only_tool_result(agent)
    assert result.is_error is False
    assert result.details == ["a", "b"]


@pytest.mark.asyncio
async def test_before_block_terminate_stops_loop(dummy_model):
    calls = 0

    def stream(model, context, options):
        nonlocal calls
        calls += 1
        return tool_call_stream(model, "echo", {"message": "hi"})

    async def before(ctx, signal):
        return BeforeToolCallResult(block=True, reason="denied", terminate=True)

    agent = Agent(stream_fn=stream, before_tool_call=before)
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])
    await agent.prompt("run")

    assert calls == 1
    result = _only_tool_result(agent)
    assert result.is_error is True
    assert result.content == [TextContent(text="denied")]


@pytest.mark.asyncio
async def test_run_tool_call_reuses_hooks_and_reports_result_metadata(dummy_model):
    before_calls = 0
    context = AgentContext()

    async def before(_context, _signal):
        nonlocal before_calls
        before_calls += 1

    async def after(_context, _signal):
        return AfterToolCallResult(
            content=[TextContent(text="overridden")],
            structured_content={"ok": True},
            usage=Usage(input=1),
        )

    result = await run_tool_call(
        ToolCall(id="call-1", name="echo", arguments={"message": "hello"}),
        RunToolCallOptions(
            tools=[EchoTool()],
            assistant_message=make_assistant_message(dummy_model, []),
            context=context,
            before_tool_call=before,
            after_tool_call=after,
        ),
    )
    assert before_calls == 1
    assert isinstance(result, AgentToolCallOutcome)
    assert result.is_error is False
    assert result.result.content == [TextContent(text="overridden")]
    assert result.result.structured_content == {"ok": True}
    assert result.result.usage == Usage(input=1)
    assert result.duration_ms is not None
    assert context.messages == []


@pytest.mark.asyncio
async def test_run_tool_call_content_override_drops_stale_structured_content(
    dummy_model,
):
    class StructuredTool(EchoTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            return AgentToolResult(
                content=[TextContent(text="old")],
                details={},
                structured_content={"old": True},
            )

    async def after(_context, _signal):
        return AfterToolCallResult(content=[TextContent(text="new")])

    result = await run_tool_call(
        ToolCall(id="call-1", name="echo", arguments={"message": "hello"}),
        RunToolCallOptions(
            tools=[StructuredTool()],
            assistant_message=make_assistant_message(dummy_model, []),
            context=AgentContext(),
            after_tool_call=after,
        ),
    )
    assert result.is_error is False
    assert result.result.content == [TextContent(text="new")]
    assert result.result.structured_content is None


@pytest.mark.asyncio
async def test_tool_usage_and_duration_reach_transcript_and_end_event(dummy_model):
    class MeteredTool(EchoTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            return AgentToolResult(
                content=[TextContent(text="metered")],
                details={},
                usage=Usage(input=2, output=3),
            )

    calls = 0
    events = []

    async def stream(model, context, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return tool_call_stream(model, "echo", {"message": "metered"})
        return text_stream(model, "done")

    agent = Agent(stream_fn=stream)
    agent.set_model(dummy_model)
    agent.set_tools([MeteredTool()])
    agent.subscribe(lambda event, _signal: events.append(event))
    await agent.prompt("run")

    result = next(
        message
        for message in agent.state.messages
        if isinstance(message, ToolResultMessage)
    )
    end = next(event for event in events if event.type == "tool_execution_end")
    assert result.usage == Usage(input=2, output=3)
    assert result.duration_ms is not None
    assert end.duration_ms == result.duration_ms
    assert end.result.usage == result.usage


@pytest.mark.asyncio
async def test_aborted_error_is_not_converted_to_tool_error(dummy_model):
    class CancelTool(AgentTool):
        name: str = "cancel"
        label: str = "cancel"
        description: str = "cancel"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            raise AbortedError("stop requested")

    with pytest.raises(AbortedError, match="stop requested"):
        await run_tool_call(
            ToolCall(id="call-1", name="cancel", arguments={}),
            RunToolCallOptions(
                tools=[CancelTool()],
                assistant_message=make_assistant_message(dummy_model, []),
                context=AgentContext(tools=[CancelTool()]),
            ),
        )


@pytest.mark.asyncio
async def test_late_tool_update_is_ignored(dummy_model):
    callbacks = []

    class UpdatingTool(AgentTool):
        name: str = "updating"
        label: str = "updating"
        description: str = "updating"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            callbacks.append(on_update)
            return AgentToolResult(content=[TextContent(text="done")], details={})

    updates = []
    outcome = await run_tool_call(
        ToolCall(id="call-1", name="updating", arguments={}),
        RunToolCallOptions(
            tools=[UpdatingTool()],
            assistant_message=make_assistant_message(dummy_model, []),
            context=AgentContext(tools=[UpdatingTool()]),
            on_update=updates.append,
        ),
    )
    assert isinstance(outcome, AgentToolCallOutcome)
    callbacks[0](AgentToolResult(content=[TextContent(text="late")], details={}))
    await asyncio.sleep(0)
    assert updates == []


@pytest.mark.asyncio
async def test_update_observer_error_becomes_tool_error(dummy_model):
    class UpdatingTool(AgentTool):
        name: str = "updating"
        label: str = "updating"
        description: str = "updating"
        parameters: dict = {"type": "object", "properties": {}}

        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            assert on_update is not None
            on_update(
                AgentToolResult(content=[TextContent(text="progress")], details={})
            )
            return AgentToolResult(content=[TextContent(text="done")], details={})

    def fail_update(_result):
        raise RuntimeError("observer failed")

    outcome = await run_tool_call(
        ToolCall(id="call-1", name="updating", arguments={}),
        RunToolCallOptions(
            tools=[UpdatingTool()],
            assistant_message=make_assistant_message(dummy_model, []),
            context=AgentContext(tools=[UpdatingTool()]),
            on_update=fail_update,
        ),
    )
    assert outcome.is_error is True
    assert outcome.result.content == [TextContent(text="observer failed")]


@pytest.mark.asyncio
async def test_sequential_config_blocks_second_tool_until_first_finishes(dummy_model):
    started = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class BlockingEchoTool(EchoTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            started.append(params["message"])
            if params["message"] == "first":
                first_started.set()
                await release_first.wait()
            return AgentToolResult(
                content=[TextContent(text=f"echo: {params['message']}")], details={}
            )

    calls = 0

    async def stream(model, context, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return multi_tool_call_stream(
                model,
                [("echo", {"message": "first"}), ("echo", {"message": "second"})],
            )
        return text_stream(model, "done")

    agent = Agent(stream_fn=stream, tool_execution="sequential")
    agent.set_model(dummy_model)
    agent.set_tools([BlockingEchoTool()])
    run = agent.start_prompt("go")
    await first_started.wait()
    assert started == ["first"]

    release_first.set()
    await run
    assert started == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_name", "second_arguments", "expected_calls", "expected_last_role"),
    [
        ("echo", {"message": "continue"}, 2, "assistant"),
        ("terminate", {}, 1, "toolResult"),
    ],
)
async def test_terminate_requires_every_result_in_batch(
    dummy_model,
    second_name,
    second_arguments,
    expected_calls,
    expected_last_role,
):
    calls = 0

    async def stream(model, context, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return multi_tool_call_stream(
                model,
                [("terminate", {}), (second_name, second_arguments)],
            )
        return text_stream(model, "continued")

    agent = Agent(stream_fn=stream)
    agent.set_model(dummy_model)
    agent.set_tools([TerminateTool(), EchoTool()])
    await agent.prompt("go")

    results = [
        message for message in agent.state.messages if message.role == "toolResult"
    ]
    assert len(results) == 2
    assert calls == expected_calls
    assert agent.state.messages[-1].role == expected_last_role
