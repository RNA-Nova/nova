"""02 - 自定义工具与参数矫正

演示：
- 继承 `AgentTool` 实现自定义工具（name / description / parameters / execute）
- JSON Schema 参数校验自动生效
- 类型矫正（coercion）：LLM 把数字给成字符串 "5" 时自动转为 5

全程 mock，离线可跑：
    python examples/02_custom_tools.py
"""

import asyncio

from nova_agent import Agent, AgentTool, AgentToolResult

from nova_ai import (
    DoneEvent,
    EventStream,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    StartEvent,
    TextContent,
    ToolCall,
    ToolCallEndEvent,
)


class SquareTool(AgentTool):
    """计算平方。注意 parameters 声明 x 是 integer。"""

    name: str = "square"
    description: str = "计算一个整数的平方"
    label: str = "Square"
    parameters: dict = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        result = params["x"] ** 2
        return AgentToolResult(
            content=[TextContent(text=f"{params['x']} 的平方是 {result}")],
            details={"result": result},
        )


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


def tool_call_stream(model: Model) -> EventStream:
    """模拟 LLM 发起 tool call：注意 x 给的是字符串 "5"（会被自动矫正为整数）。"""
    from nova_ai import AssistantMessage

    tool_call = ToolCall(id="tc-1", name="square", arguments={"x": "5"})
    partial = AssistantMessage(
        role="assistant",
        content=[tool_call],
        api=model.api,
        provider=model.provider,
        model=model.id,
        stop_reason="toolUse",
    )
    stream = EventStream(
        is_complete=lambda e: e.type == "done",
        extract_result=lambda e: e.message,
    )
    stream.push(StartEvent(partial=partial))
    stream.push(ToolCallEndEvent(content_index=0, tool_call=tool_call, partial=partial))
    stream.push(DoneEvent(reason="toolUse", message=partial))
    stream.end()
    return stream


def text_stream(model: Model, text: str) -> EventStream:
    from nova_ai import AssistantMessage

    partial = AssistantMessage(
        role="assistant",
        content=[TextContent(text=text)],
        api=model.api,
        provider=model.provider,
        model=model.id,
        stop_reason="stop",
    )
    stream = EventStream(
        is_complete=lambda e: e.type == "done",
        extract_result=lambda e: e.message,
    )
    stream.push(StartEvent(partial=partial))
    stream.push(DoneEvent(reason="stop", message=partial))
    stream.end()
    return stream


async def main():
    model = make_model()
    step = 0

    def stream_fn(m, context, options):
        nonlocal step
        step += 1
        # 第一轮：LLM 发起 tool call；第二轮：根据工具结果给出最终回复
        return tool_call_stream(m) if step == 1 else text_stream(m, "算完了")

    agent = Agent(stream_fn=stream_fn)
    agent.set_model(model)
    agent.set_tools([SquareTool()])

    agent.subscribe(lambda event, signal=None: print(f"event: {event.type}"))

    await agent.prompt("帮我算 5 的平方")

    for message in agent.state.messages:
        if message.role == "toolResult":
            print("工具结果:", message.content[0].text, "（字符串 '5' 已被矫正为整数）")


if __name__ == "__main__":
    asyncio.run(main())
