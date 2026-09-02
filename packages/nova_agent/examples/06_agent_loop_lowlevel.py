"""06 - 低层 agent_loop：EventStream 形式的循环

`Agent` 类之外，还可以直接用低层 facade：
- `agent_loop(prompts, context, config, ...)` → AgentEventStream
- `agent_loop_continue(context, config, ...)` → AgentEventStream

适合想自己管理状态（不用 Agent 类）的场景：事件全部从流里读，
结果通过 `stream.result()` 获取。

离线可跑：
    python examples/06_agent_loop_lowlevel.py
"""

import asyncio

from nova_agent import AgentContext, AgentLoopConfig, agent_loop

from nova_ai import (
    DoneEvent,
    EventStream,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    SimpleStreamOptions,
    StartEvent,
    TextContent,
    UserMessage,
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
    config = AgentLoopConfig(stream_options=SimpleStreamOptions(), model=model)
    context = AgentContext(system_prompt="你是一个简洁的助手", messages=[])

    stream = agent_loop(
        [UserMessage(role="user", content="你好")],
        context,
        config,
        stream_fn=lambda m, c, o: text_stream(m, "你好！（低层调用）"),
    )

    # 边消费事件边观察
    async for event in stream:
        print(f"event: {event.type}")

    # result() 拿到本次 run 产出的全部新消息
    new_messages = await stream.result()
    print("新消息角色序列:", [m.role for m in new_messages])
    print("助手回复:", new_messages[-1].content[0].text)

    # context.messages 也被同步更新（注意：这是低层 API 的约定）
    print("context 消息数:", len(context.messages))


if __name__ == "__main__":
    asyncio.run(main())
