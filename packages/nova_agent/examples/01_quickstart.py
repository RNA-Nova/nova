"""01 - Agent 最小用法

演示 Agent 的核心循环：创建 → 订阅事件 → prompt → 状态变化。
全程使用 mock stream_fn，不依赖任何 API Key。

运行：
    python examples/01_quickstart.py
"""

import asyncio

from nova_agent import Agent

from nova_ai import (
    DoneEvent,
    EventStream,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    StartEvent,
    TextContent,
)


def make_demo_model() -> Model:
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


def make_text_stream(model: Model, text: str) -> EventStream:
    """mock stream_fn 的返回：一个固定文本的助手事件流。"""
    from nova_ai import TextDeltaEvent, TextEndEvent

    partial = make_assistant_message(model, text)
    stream = EventStream(
        is_complete=lambda e: e.type == "done",
        extract_result=lambda e: e.message,
    )
    stream.push(StartEvent(partial=partial))
    stream.push(TextDeltaEvent(content_index=0, delta=text, partial=partial))
    stream.push(TextEndEvent(content_index=0, content=text, partial=partial))
    stream.push(DoneEvent(reason="stop", message=partial))
    stream.end()
    return stream


def make_assistant_message(model: Model, text: str):
    from nova_ai import AssistantMessage

    return AssistantMessage(
        role="assistant",
        content=[TextContent(text=text)],
        api=model.api,
        provider=model.provider,
        model=model.id,
        stop_reason="stop",
    )


async def main():
    model = make_demo_model()
    agent = Agent(stream_fn=lambda m, c, o: make_text_stream(m, "你好，我是 Agent"))
    agent.set_model(model)

    # 订阅事件：agent_start / turn_start / message_* / turn_end / agent_end
    agent.subscribe(lambda event, signal=None: print(f"event: {event.type}"))

    await agent.prompt("你好")

    print("最终消息数:", len(agent.state.messages))
    print("助手回复:", agent.state.messages[-1].content[0].text)
    print("is_streaming:", agent.state.is_streaming)


if __name__ == "__main__":
    asyncio.run(main())
