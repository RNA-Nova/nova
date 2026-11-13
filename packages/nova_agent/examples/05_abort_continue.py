"""05 - abort 与 continue_

- `agent.abort()`：取消当前 run（通过 AbortSignal 传播到 stream 与工具）
- `agent.wait_for_idle()`：等待当前 run 结束（shield 语义：等待方被取消不影响 run）
- `agent.continue_()`：从当前上下文继续（重试、处理排队的 steering/follow_up）

离线可跑：
    python examples/05_abort_continue.py
"""

import asyncio
from contextlib import suppress

from nova_ai import EventStream
from nova_protocol import (
    AbortedError,
    AgentToolResult,
    DoneEvent,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    StartEvent,
    TextContent,
    ToolCall,
    ToolCallEndEvent,
    UserMessage,
)

from nova_agent import Agent, AgentTool

started = asyncio.Event()


class SlowTool(AgentTool):
    """会检查 abort signal 的慢工具。"""

    name: str = "slow"
    description: str = "慢速工具"
    label: str = "Slow"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        started.set()
        for _ in range(100):
            if signal and signal.aborted:
                raise AbortedError("Operation aborted")
            await asyncio.sleep(0.01)
        return AgentToolResult(content=[TextContent(text="done")], details={})


def make_model() -> Model:
    return Model(
        id="demo",
        name="demo",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.OPENAI,
        base_url="https://example.com",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=8192,
        max_tokens=4096,
    )


def make_stream(model: Model, with_tool_call: bool) -> EventStream:
    from nova_protocol import AssistantMessage

    content = (
        [ToolCall(id="tc-1", name="slow", arguments={})]
        if with_tool_call
        else [TextContent(text="ok")]
    )
    partial = AssistantMessage(
        role="assistant",
        content=content,
        api=model.api,
        provider=model.provider,
        model=model.id,
        stop_reason="toolUse" if with_tool_call else "stop",
    )
    stream = EventStream(
        is_complete=lambda e: e.type == "done",
        extract_result=lambda e: e.message,
    )
    stream.push(StartEvent(partial=partial))
    if with_tool_call:
        stream.push(
            ToolCallEndEvent(content_index=0, tool_call=content[0], partial=partial)
        )
    stream.push(DoneEvent(reason=partial.stop_reason, message=partial))
    stream.end()
    return stream


async def main():
    model = make_model()
    step = 0

    def stream_fn(m, context, options):
        nonlocal step
        step += 1
        return make_stream(m, with_tool_call=(step == 1))

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(model)
    agent.set_tools([SlowTool()])

    # 等工具真正开始后 abort；另取消一个 idle waiter，验证 shield 不传染给 run
    run_task = asyncio.create_task(agent.prompt("开始"))
    await started.wait()
    agent.abort()
    idle_waiter = asyncio.create_task(agent.wait_for_idle())
    idle_waiter.cancel()
    with suppress(asyncio.CancelledError):
        await idle_waiter
    await run_task  # run 自行完成 aborted 收尾，不被 waiter 的取消影响

    print("abort 后 is_streaming:", agent.state.is_streaming)
    print("最后一条消息 stop_reason:", agent.state.messages[-1].stop_reason)

    # continue_ 的合法路径之一：assistant 结尾时，先排队 follow_up 再继续
    agent.follow_up(UserMessage(role="user", content="我们继续"))
    await agent.continue_()
    print("continue_ 后最后一条消息:", agent.state.messages[-1].content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
