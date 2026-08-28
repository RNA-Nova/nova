"""02 - 流式事件类型详解

`stream()` / `stream_simple()` 返回的 AssistantMessageEventStream 按顺序产出：
    start → (text_start → text_delta* → text_end)? → (thinking_*)?
          → (toolcall_start → toolcall_delta* → toolcall_end)* → done | error

本例用手工构造的流展示全部事件种类，以及如何边消费边读最终消息。

运行：
    python examples/02_stream_events.py
"""

import asyncio

from nova_ai import (
    AssistantMessage,
    DoneEvent,
    EventStream,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolCallEndEvent,
)


def build_demo_stream(model: Model) -> EventStream:
    """构造一个包含 thinking + text + toolcall 的完整事件流。"""
    stream = EventStream(
        is_complete=lambda e: e.type == "done",
        extract_result=lambda e: e.message,
    )
    tool_call = ToolCall(id="tc-1", name="get_weather", arguments={"city": "北京"})
    partial = AssistantMessage(
        role="assistant",
        content=[
            ThinkingContent(thinking="用户问天气，先查工具……"),
            TextContent(text="北京今天晴。"),
            tool_call,
        ],
        api=model.api,
        provider=model.provider,
        model=model.id,
        stop_reason="toolUse",
    )
    stream.push(StartEvent(partial=partial))
    stream.push(ThinkingStartEvent(content_index=0, partial=partial))
    stream.push(
        ThinkingDeltaEvent(content_index=0, delta="用户问天气", partial=partial)
    )
    stream.push(
        ThinkingEndEvent(content_index=0, content="用户问天气……", partial=partial)
    )
    stream.push(TextDeltaEvent(content_index=1, delta="北京今天晴。", partial=partial))
    stream.push(TextEndEvent(content_index=1, content="北京今天晴。", partial=partial))
    stream.push(ToolCallEndEvent(content_index=2, tool_call=tool_call, partial=partial))
    stream.push(DoneEvent(reason="toolUse", message=partial))
    stream.end()
    return stream


async def main():
    from nova_ai import KnownApi, KnownProvider, ModelCost

    model = Model(
        id="demo",
        name="demo",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.OPENAI,
        base_url="https://example.com",
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=8192,
        max_tokens=4096,
    )

    stream = build_demo_stream(model)
    async for event in stream:
        print(f"event: {event.type}")

    final = await stream.result()
    print("最终消息 stop_reason:", final.stop_reason)
    print("内容块类型:", [c.type for c in final.content])


if __name__ == "__main__":
    asyncio.run(main())
