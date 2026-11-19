"""Agent 转录、请求准备与回合调度的行为契约。"""

import pytest
from _helpers.agent import EchoTool, final_stream, text_stream, tool_call_stream
from _helpers.messages import assistant_message, user_message
from nova_protocol import (
    ClientAttribution,
    ClientIdentity,
    ModelThinkingLevel,
    SystemMessage,
    Tool,
    ToolReference,
    UserMessage,
)

from nova_agent import Agent, AgentLoopTurnUpdate, AgentRequestUpdate, AgentTurnDecision


@pytest.mark.asyncio
async def test_initial_prompt_tools_and_reset_use_transcript_baseline(dummy_model):
    agent = Agent(
        stream_fn=lambda m, c, o: text_stream(m, "ok"),
        initial_state={"system_prompt": "base", "tools": [EchoTool()]},
    )
    initial = agent.state.messages[0]
    assert isinstance(initial, SystemMessage)
    assert initial.content == "base"
    assert [tool.name for tool in initial.tools_added or []] == ["echo"]
    assert agent.state.system_prompt == "base"

    agent.append_message(UserMessage(content="later"))
    agent.reset()
    assert len(agent.state.messages) == 1
    assert isinstance(agent.state.messages[0], SystemMessage)
    assert agent.state.system_prompt == "base"


@pytest.mark.asyncio
async def test_tool_removal_is_recorded_as_transcript_delta(dummy_model):
    agent = Agent(
        stream_fn=lambda m, c, o: text_stream(m, "ok"),
        initial_state={"tools": [EchoTool()]},
    )
    agent.set_model(dummy_model)
    await agent.prompt("first")
    agent.set_tools([])
    await agent.prompt("second")
    removals = [
        message
        for message in agent.state.messages
        if isinstance(message, SystemMessage) and message.tools_removed
    ]
    assert len(removals) == 1
    assert [ref.name for ref in removals[0].tools_removed or []] == ["echo"]


@pytest.mark.asyncio
async def test_prepare_request_runs_on_first_request_and_updates_runtime(dummy_model):
    other_model = dummy_model.model_copy(update={"id": "other-model"})
    seen = []
    prepare_calls = 0

    async def prepare(request, _signal):
        nonlocal prepare_calls
        prepare_calls += 1
        return AgentRequestUpdate(
            model=other_model, thinking_level=ModelThinkingLevel.HIGH
        )

    async def stream(model, context, options):
        seen.append((model.id, options.reasoning))
        return text_stream(model, "ok")

    agent = Agent(stream_fn=stream, prepare_request=prepare)
    agent.set_model(dummy_model)
    await agent.prompt("run")
    assert prepare_calls == 1
    assert seen == [("other-model", "high")]
    final = assistant_message(agent.state.messages[-1])
    assert final.thinking_level is ModelThinkingLevel.HIGH


@pytest.mark.asyncio
async def test_finish_turn_end_runs_before_turn_end_and_preserves_queues(dummy_model):
    observed = []
    stream_calls = 0

    async def stream(model, context, options):
        nonlocal stream_calls
        stream_calls += 1
        return text_stream(model, "done")

    agent: Agent

    async def finish(turn, _signal):
        observed.append("finish")
        agent.steer(UserMessage(content="queued"))
        return AgentTurnDecision(action="end")

    agent = Agent(stream_fn=stream, finish_turn=finish)
    agent.set_model(dummy_model)
    run = agent.prompt("run")
    await run
    event_types = [event.type async for event in run.events]
    assert observed == ["finish"]
    assert event_types[-2:] == ["turn_end", "agent_end"]
    assert stream_calls == 1
    assert agent.has_queued_messages()


@pytest.mark.asyncio
async def test_finish_turn_continue_adds_one_context_only_request(dummy_model):
    calls = 0
    finish_calls = 0

    async def stream(model, context, options):
        nonlocal calls
        calls += 1
        return text_stream(model, f"turn-{calls}")

    async def finish(_turn, _signal):
        nonlocal finish_calls
        finish_calls += 1
        if finish_calls == 1:
            return AgentTurnDecision(action="continue")
        return None

    agent = Agent(stream_fn=stream, finish_turn=finish)
    agent.set_model(dummy_model)
    await agent.prompt("run")
    assert calls == 2
    assert finish_calls == 2


@pytest.mark.asyncio
async def test_prepare_next_turn_appends_messages_only_when_continuing(dummy_model):
    calls = 0
    prepare_calls = 0

    async def stream(model, context, options):
        nonlocal calls
        calls += 1
        if calls == 1:
            return tool_call_stream(model, "echo", {"message": "next"})
        return text_stream(model, "done")

    async def prepare(turn, _signal):
        nonlocal prepare_calls
        prepare_calls += 1
        return AgentLoopTurnUpdate(messages=[UserMessage(content="prepared")])

    agent = Agent(stream_fn=stream, prepare_next_turn=prepare)
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])
    await agent.prompt("run")
    assert prepare_calls == 1
    assert any(
        isinstance(message, UserMessage) and message.content == "prepared"
        for message in agent.state.messages
    )


@pytest.mark.asyncio
async def test_error_turn_ignores_continue_decision(dummy_model):
    calls = 0

    async def stream(model, context, options):
        nonlocal calls
        calls += 1
        return final_stream(model, "failed", stop_reason="error")

    async def finish(_turn, _signal):
        return AgentTurnDecision(action="continue")

    agent = Agent(stream_fn=stream, finish_turn=finish)
    agent.set_model(dummy_model)
    await agent.prompt("run")
    assert calls == 1
    assert assistant_message(agent.state.messages[-1]).stop_reason == "error"


@pytest.mark.asyncio
async def test_identity_and_provider_observer_reach_stream_options(dummy_model):
    seen = {}

    async def observe(event, _identity):
        seen["event"] = event

    async def stream(model, context, options):
        seen["identity"] = options.identity
        seen["observer"] = options.on_provider_stream_event
        return text_stream(model, "ok")

    identity = ClientIdentity(
        attribution=ClientAttribution(name="custom", url="https://example.com")
    )
    agent = Agent(
        stream_fn=stream,
        identity=identity,
        on_provider_stream_event=observe,
    )
    agent.set_model(dummy_model)
    await agent.prompt("run")
    assert seen["identity"].attribution.name == "custom"
    assert seen["observer"] is observe


def test_peek_queued_messages_does_not_consume(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    agent.steer(UserMessage(content="first"))
    agent.steer(UserMessage(content="second"))
    preview = agent.peek_queued_messages()
    assert user_message(preview[0]).content == "first"
    assert user_message(agent.peek_queued_messages()[0]).content == "first"


@pytest.mark.asyncio
async def test_system_only_transcript_cannot_continue(dummy_model):
    agent = Agent(stream_fn=lambda m, c, o: text_stream(m, "ok"))
    agent.replace_messages([SystemMessage(content="only system")])
    with pytest.raises(RuntimeError, match="No messages"):
        await agent.continue_()


@pytest.mark.asyncio
async def test_finish_turn_hook_receives_current_run_signal(dummy_model):
    seen = {}

    async def finish(ctx, signal):
        seen["signal"] = signal
        return AgentTurnDecision(action="end")

    agent = Agent(
        stream_fn=lambda model, context, options: final_stream(model, "done"),
        finish_turn=finish,
    )
    agent.set_model(dummy_model)
    await agent.prompt("run")

    assert seen["signal"] is not None
    assert seen["signal"].aborted is False


@pytest.mark.asyncio
async def test_prepare_request_can_replace_request_context(dummy_model):
    seen = []

    async def prepare(request, _signal):
        return AgentRequestUpdate(
            context=type(request.context)(
                messages=[UserMessage(content="prepared")],
                tools=request.context.tools,
            )
        )

    async def stream(model, context, options):
        seen.extend(context.messages)
        return text_stream(model, "ok")

    agent = Agent(stream_fn=stream, prepare_request=prepare)
    agent.set_model(dummy_model)
    await agent.prompt("original")

    assert [message.content for message in seen] == ["prepared"]


@pytest.mark.asyncio
async def test_pending_system_message_merges_current_tool_declaration(dummy_model):
    agent = Agent(
        stream_fn=lambda m, c, o: text_stream(m, "ok"),
        initial_state={"system_prompt": "base"},
    )
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])

    await agent.prompt(
        [
            SystemMessage(content="", sections={"skills": "<skills>x</skills>"}),
            UserMessage(content="hi"),
        ]
    )

    pending = agent.state.messages[1]
    assert isinstance(pending, SystemMessage)
    assert pending.sections == {"skills": "<skills>x</skills>"}
    assert [tool.name for tool in pending.tools_added or []] == ["echo"]
    assert pending.tools_removed is None


@pytest.mark.asyncio
async def test_pending_tool_declarations_are_rewritten_to_executable_set(dummy_model):
    agent = Agent(
        stream_fn=lambda m, c, o: text_stream(m, "ok"),
        initial_state={"tools": [EchoTool()]},
    )
    agent.set_model(dummy_model)
    pending_claim = SystemMessage(
        content="",
        sections={"note": "<note>x</note>"},
        tools_added=[Tool(name="second", description="second", parameters={})],
        tools_removed=[ToolReference(name="echo")],
    )

    await agent.prompt([pending_claim, UserMessage(content="hi")])

    pending = agent.state.messages[1]
    assert isinstance(pending, SystemMessage)
    assert pending.sections == {"note": "<note>x</note>"}
    assert pending.tools_added is None
    assert pending.tools_removed is None
    baseline = agent.state.messages[0]
    assert isinstance(baseline, SystemMessage)
    assert [tool.name for tool in baseline.tools_added or []] == ["echo"]
