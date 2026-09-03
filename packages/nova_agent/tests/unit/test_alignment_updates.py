"""本轮对齐修正的回归测试。

覆盖：非 dict details 透传（B1）、reset 运行中守卫（B2）、
BeforeToolCallResult.terminate、should_stop_after_turn 收到 signal、
steering/follow_up mode property 化、set_default_stream_fn 注册点。
"""

import asyncio

import pytest
from helpers import EchoTool, final_stream, tool_call_stream
from nova_agent import (
    Agent,
    BeforeToolCallResult,
    set_default_stream_fn,
)
from nova_agent.types import AgentToolResult, ShouldStopAfterTurnContext
from nova_agent.types.tool import AgentTool
from nova_ai import TextContent, UserMessage

# ---------------------------------------------------------------------------
# B1：非 dict details 透传
# ---------------------------------------------------------------------------


def _tool_then_text_stream(model, tool_name, arguments, text="done"):
    """首轮返回 tool call，次轮返回文本收尾（两段式 mock）。"""
    state = {"n": 0}

    def _stream(m, c, o):
        if state["n"] == 0:
            state["n"] += 1
            return tool_call_stream(m, tool_name, arguments)
        return final_stream(m, text)

    return _stream


class _ListDetailsTool(AgentTool):
    """details 返回 list 的工具（pi 语义：details 为任意 JSON 值）。"""

    name: str = "list_details"
    description: str = "returns list details"
    label: str = "ListDetails"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text="ok")],
            details=["a", "b"],
        )


@pytest.mark.asyncio
async def test_non_dict_details_passthrough(dummy_model):
    """工具返回非 dict details：原样透传为 toolResult.details，不被改写为错误。"""
    agent = Agent(stream_fn=_tool_then_text_stream(dummy_model, "list_details", {}))
    agent.set_model(dummy_model)
    agent.set_tools([_ListDetailsTool()])
    await agent.prompt("run")

    tool_results = [m for m in agent.state.messages if m.role == "toolResult"]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is False
    assert tool_results[0].details == ["a", "b"]


# ---------------------------------------------------------------------------
# B2：reset 运行中守卫
# ---------------------------------------------------------------------------


class _HangingTool(AgentTool):
    """挂起 0.5s 的工具，保证 reset 调用发生在 run 期间。"""

    name: str = "hang"
    description: str = "hang"
    label: str = "Hang"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        await asyncio.sleep(0.5)
        return AgentToolResult(content=[TextContent(text="done")], details={})


@pytest.mark.asyncio
async def test_reset_during_run_raises(dummy_model):
    """运行中 reset：抛 RuntimeError（对齐 pi agent.ts:334-336）。"""
    # 两段式：首轮 tool call（工具挂起 0.5s），次轮文本收尾——lambda 每轮都
    # 返回 tool call 会让循环无限执行，这正是原实现会踩的坑
    calls = []

    def hang_stream(m, c, o):
        calls.append(1)
        if len(calls) == 1:
            return tool_call_stream(m, "hang", {})
        return final_stream(m, "done")

    agent = Agent(stream_fn=hang_stream)
    agent.set_model(dummy_model)
    agent.set_tools([_HangingTool()])

    _run_task = asyncio.create_task(agent.prompt("run"))  # 持引用防 GC；由 abort+wait_for_idle 收尾
    await asyncio.sleep(0.1)  # 等 run 起来（工具挂起中，is_streaming=True）
    assert agent.state.is_streaming is True

    with pytest.raises(RuntimeError, match="already processing"):
        agent.reset()

    agent.abort()
    await agent.wait_for_idle()


def test_reset_when_idle_clears_state():
    agent = Agent()
    agent.steer(UserMessage(role="user", content=[]))
    agent.reset()
    assert agent.state.messages == []
    assert agent.has_queued_messages() is False


# ---------------------------------------------------------------------------
# BeforeToolCallResult.terminate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_block_terminate_stops_loop(dummy_model):
    """before 钩子 block+terminate：工具被拦截，批终止，不再发起第二轮 LLM 调用。"""
    stream_calls = []

    def stream(m, c, o):
        stream_calls.append(1)
        return tool_call_stream(m, "echo", {"message": "hi"})

    async def before(ctx, signal):
        return BeforeToolCallResult(block=True, reason="denied", terminate=True)

    agent = Agent(stream_fn=stream, before_tool_call=before)
    agent.set_model(dummy_model)
    agent.set_tools([EchoTool()])
    await agent.prompt("run")

    # 拦截生效：批终止，没有第二轮 LLM 调用
    assert len(stream_calls) == 1
    tool_results = [m for m in agent.state.messages if m.role == "toolResult"]
    assert len(tool_results) == 1
    assert tool_results[0].is_error is True
    assert tool_results[0].content[0].text == "denied"


# ---------------------------------------------------------------------------
# should_stop_after_turn 收到 signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_stop_hook_receives_signal(dummy_model):
    """should_stop_after_turn 钩子第二参收到 Agent 当前 run 的 signal。"""
    seen = {}

    async def should_stop(ctx: ShouldStopAfterTurnContext, signal=None):
        seen["signal"] = signal
        return True

    agent = Agent(
        stream_fn=lambda m, c, o: final_stream(m, "done"),
        should_stop_after_turn=should_stop,
    )
    agent.set_model(dummy_model)
    await agent.prompt("run")
    assert seen["signal"] is not None
    assert seen["signal"].aborted is False


# ---------------------------------------------------------------------------
# steering / follow_up mode property
# ---------------------------------------------------------------------------


def test_steering_mode_property_writes_queue():
    agent = Agent(steering_mode="one-at-a-time")
    agent.steering_mode = "all"
    assert agent.steering_mode == "all"
    assert agent._steering_queue.mode == "all"

    agent.follow_up_mode = "all"
    assert agent.follow_up_mode == "all"
    assert agent._follow_up_queue.mode == "all"


# ---------------------------------------------------------------------------
# set_default_stream_fn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_default_stream_fn_used_when_stream_fn_omitted(dummy_model):
    """未显式传 stream_fn 时，注册点上的默认函数被使用（对齐 pi stream-fn.ts）。"""
    calls = []

    def default_fn(m, c, o):
        calls.append(1)
        return final_stream(m, "via default")

    set_default_stream_fn(default_fn)
    try:
        agent = Agent(stream_fn=None)
        agent.set_model(dummy_model)
        await agent.prompt("run")
        assert calls == [1]
    finally:
        set_default_stream_fn(None)


def test_builtin_fallback_when_no_default_registered():
    """未注册默认且未显式注入时：回退内置目录（nova 特有）。"""
    from nova_agent.stream_fn import builtin_fallback_stream_fn

    fn = builtin_fallback_stream_fn()
    assert callable(fn)
    # 不做进程级缓存：网关是可变运行时容器，跨 Agent 共享会陈旧化
    assert builtin_fallback_stream_fn() is not fn
