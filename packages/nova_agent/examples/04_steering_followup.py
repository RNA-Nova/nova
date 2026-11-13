"""04 - Steering 与 Follow-up 队列

- `steer(message)`：agent 运行中插入消息，当前轮工具执行完后注入
- `follow_up(message)`：agent 即将停止时排队继续的消息
- 队列模式：`"one-at-a-time"`（每次 drain 一条）/ `"all"`（一次 drain 全部）

离线可跑：
    python examples/04_steering_followup.py
"""

import asyncio

from nova_ai import EventStream
from nova_protocol import (
    DoneEvent,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    StartEvent,
    TextContent,
    UserMessage,
)

from nova_agent import Agent


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
    from nova_protocol import AssistantMessage

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
    calls = []

    def stream_fn(m, context, options):
        # 每一轮回复当前是第几轮
        calls.append(len(context.messages))
        return text_stream(m, f"第 {len(calls)} 轮回复")

    # one-at-a-time：每次 drain 只注入一条排队消息
    agent = Agent(stream_fn=stream_fn, steering_mode="one-at-a-time")
    agent.set_model(model)

    # 运行中排队两条 steering：首个 turn 开始时入队，后续逐条注入
    queued = False

    def queue_during_run(event, signal):
        nonlocal queued
        if event.type == "turn_start" and not queued:
            queued = True
            agent.steer(UserMessage(role="user", content="补充一点 A"))
            agent.steer(UserMessage(role="user", content="补充一点 B"))

    agent.subscribe(queue_during_run)
    await agent.prompt("开始")

    print("总轮数:", len(calls), "（one-at-a-time 每轮只 drain 一条）")
    user_messages = [m.content for m in agent.state.messages if m.role == "user"]
    print("用户消息序列:", user_messages)

    # follow_up：自然回合完成、agent 即将停止时入队并继续
    agent2 = Agent(stream_fn=stream_fn)
    agent2.set_model(model)
    queued_follow_up = False

    def queue_before_stop(event, signal):
        nonlocal queued_follow_up
        if event.type == "turn_end" and not queued_follow_up:
            queued_follow_up = True
            agent2.follow_up(UserMessage(role="user", content="再补充一点 C"))

    agent2.subscribe(queue_before_stop)
    await agent2.prompt("第二轮开始")

    user_messages2 = [m.content for m in agent2.state.messages if m.role == "user"]
    print("agent2 用户消息序列:", user_messages2)


if __name__ == "__main__":
    asyncio.run(main())
