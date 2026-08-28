"""03 - Hooks：四个扩展点

Agent 提供的四个 hook：
- `before_tool_call`：工具执行前拦截（可 block）
- `after_tool_call`：工具执行后改写结果
- `prepare_next_turn`：每轮结束后调整下一轮（换模型/思考级别/上下文）
- `should_stop_after_turn`：每轮结束后优雅停止

离线可跑：
    python examples/03_hooks.py
"""

import asyncio

from nova_agent import (
    Agent,
    AgentLoopTurnUpdate,
    AgentTool,
    AgentToolResult,
    BeforeToolCallResult,
)

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


class ReadFileTool(AgentTool):
    name: str = "read_file"
    description: str = "读取文件内容"
    label: str = "ReadFile"
    parameters: dict = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text=f"<content of {params['path']}>")],
            details={},
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


def make_stream(model: Model, with_tool_call: bool, text: str = "完成") -> EventStream:
    from nova_ai import AssistantMessage

    content = []
    if with_tool_call:
        content.append(
            ToolCall(id="tc-1", name="read_file", arguments={"path": "/etc/passwd"})
        )
    else:
        content.append(TextContent(text=text))
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

    # before_tool_call：拦截敏感路径
    def before(ctx, signal):
        if ctx.args.get("path", "").startswith("/etc/"):
            print("[hook] before_tool_call: 拦截敏感路径", ctx.args["path"])
            return BeforeToolCallResult(block=True, reason="不允许读取 /etc/ 下的文件")
        return None

    # after_tool_call：给工具结果加前缀
    def after(ctx, signal):
        print("[hook] after_tool_call: 原结果 ->", ctx.result.content[0].text)

    # prepare_next_turn：观察每轮结束
    def prepare_next_turn(ctx, signal):
        print(f"[hook] prepare_next_turn: 第 {ctx.turn_index} 轮结束")
        return AgentLoopTurnUpdate()

    agent = Agent(
        stream_fn=stream_fn,
        before_tool_call=before,
        after_tool_call=after,
        prepare_next_turn=prepare_next_turn,
    )
    agent.set_model(model)
    agent.set_tools([ReadFileTool()])

    await agent.prompt("读一下 /etc/passwd")

    tool_result = [m for m in agent.state.messages if m.role == "toolResult"][0]
    print("最终工具结果（已被拦截）:", tool_result.content[0].text)
    print("is_error:", tool_result.is_error)


if __name__ == "__main__":
    asyncio.run(main())
