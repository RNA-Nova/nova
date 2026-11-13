"""03 - Hooks：工具、请求与回合的五个扩展点

Agent 提供的五个 hook：
- `before_tool_call`：工具执行前拦截（可 block）
- `after_tool_call`：工具执行后改写结果
- `prepare_request`：每次模型请求前调整模型/思考级别/上下文
- `prepare_next_turn`：确定继续后调整下一轮，并可追加转录消息
- `finish_turn`：回合完成后决定自然调度、显式继续或结束

离线可跑：
    python examples/03_hooks.py
"""

import asyncio

from nova_ai import EventStream
from nova_protocol import (
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
)

from nova_agent import (
    AfterToolCallResult,
    Agent,
    AgentLoopTurnUpdate,
    AgentTool,
    BeforeToolCallResult,
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


def make_stream(
    model: Model, with_tool_call: bool, text: str = "完成", path: str = "/etc/passwd"
) -> EventStream:
    from nova_protocol import AssistantMessage

    content = []
    if with_tool_call:
        content.append(ToolCall(id="tc-1", name="read_file", arguments={"path": path}))
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
        if step == 1:
            return make_stream(m, with_tool_call=True, path="/etc/passwd")
        if step == 2:
            return make_stream(m, with_tool_call=True, path="/tmp/demo.txt")
        return make_stream(m, with_tool_call=False)

    # before_tool_call：拦截敏感路径
    def before(ctx, signal):
        if ctx.args.get("path", "").startswith("/etc/"):
            print("[hook] before_tool_call: 拦截敏感路径", ctx.args["path"])
            return BeforeToolCallResult(block=True, reason="不允许读取 /etc/ 下的文件")
        return None

    # after_tool_call：给成功工具的结果加审计前缀
    def after(ctx, signal):
        original = ctx.result.content[0].text
        print("[hook] after_tool_call: 原结果 ->", original)
        return AfterToolCallResult(content=[TextContent(text=f"[audited] {original}")])

    # prepare_request：每次请求前观察当前模型
    def prepare_request(ctx, signal):
        print(f"[hook] prepare_request: 模型 {ctx.model.id}")

    # prepare_next_turn：只在确定还有下一轮时运行
    def prepare_next_turn(ctx, signal):
        print(f"[hook] prepare_next_turn: 准备第 {ctx.turn_index + 1} 轮")
        return AgentLoopTurnUpdate()

    # finish_turn：观察回合收尾；None 表示保持自然调度
    def finish_turn(ctx, signal):
        print(f"[hook] finish_turn: 第 {ctx.turn_index} 轮完成")

    agent = Agent(
        stream_fn=stream_fn,
        before_tool_call=before,
        after_tool_call=after,
        prepare_request=prepare_request,
        prepare_next_turn=prepare_next_turn,
        finish_turn=finish_turn,
    )
    agent.set_model(model)
    agent.set_tools([ReadFileTool()])

    await agent.prompt("读一下 /etc/passwd")

    tool_results = [m for m in agent.state.messages if m.role == "toolResult"]
    blocked = next(m for m in tool_results if m.is_error)
    print("被 before 拦截的结果:", blocked.content[0].text)
    print("is_error:", blocked.is_error)
    succeeded = next(m for m in tool_results if not m.is_error)
    print("被 after 改写的结果:", succeeded.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
