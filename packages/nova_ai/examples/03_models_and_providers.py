"""03 - Models 注册表与 Provider 体系

演示：
- `builtin_models()`：内置 provider（volcengine / moonshotai / kimi-coding）开箱即用；
- `create_models()` + `create_provider()`：自建注册表，注册自定义 provider；
- 动态模型目录：`fetch_models` 回调让 provider 的模型列表可刷新。

运行：
    python examples/03_models_and_providers.py
"""

import asyncio
import os
from types import SimpleNamespace

from nova_ai import (
    AssistantMessage,
    Context,
    DoneEvent,
    EventStream,
    Model,
    StartEvent,
    TextContent,
    UserMessage,
    builtin_models,
    create_models,
    create_provider,
)
from nova_ai.types.enums import KnownApi, KnownProvider
from nova_ai.types.model import ModelCost


def make_echo_api():
    """最小协议实现：把用户最后一条消息回显。"""

    def stream_simple(model, context, options=None):
        stream = EventStream(
            is_complete=lambda e: e.type == "done",
            extract_result=lambda e: e.message,
        )
        last = context.messages[-1]
        text = last.content if isinstance(last.content, str) else "..."
        partial = AssistantMessage(
            role="assistant",
            content=[TextContent(text=f"echo: {text}")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            stop_reason="stop",
        )
        stream.push(StartEvent(partial=partial))
        stream.push(DoneEvent(reason="stop", message=partial))
        stream.end()
        return stream

    return SimpleNamespace(stream=stream_simple, stream_simple=stream_simple)


def demo_builtin():
    models = builtin_models()
    for provider in models.get_providers():
        print(f"[builtin] {provider.id}: {len(provider.get_models())} 个模型")


async def demo_custom():
    # 示例环境临时注入 demo key：Models 的 auth 链要求 provider 可解析出凭证
    os.environ.setdefault("DEMO_API_KEY", "demo-key")

    my_model = Model(
        id="echo-1",
        name="Echo",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider="my-provider",
        base_url="https://example.com",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=8192,
        max_tokens=4096,
    )
    from nova_ai.auth.helpers import env_api_key_auth
    from nova_ai.types.auth import ProviderAuth

    provider = create_provider(
        id="my-provider",
        name="My Provider",
        models=[my_model],
        api=make_echo_api(),
        auth=ProviderAuth(api_key=env_api_key_auth("Demo API key", ["DEMO_API_KEY"])),
    )
    models = create_models()
    models.set_provider(provider)

    found = models.get_model("my-provider", "echo-1")
    print("[custom] 注册并查回模型:", found.id)

    reply = await models.stream_simple(
        my_model, Context(messages=[UserMessage(role="user", content="你好")])
    ).result()
    print("[custom] 调用结果:", reply.content[0].text)


def demo_dynamic():
    async def fetch_models(context):
        """真实场景里是 GET /v1/models；这里返回固定列表。"""
        return [
            Model(
                id="dynamic-1",
                name="Dynamic",
                api=KnownApi.OPENAI_COMPLETIONS,
                provider="dyn",
                base_url="https://example.com",
                reasoning=False,
                input_types=["text"],
                cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
                context_window=8192,
                max_tokens=4096,
            )
        ]

    provider = create_provider(
        id="dyn",
        name="Dynamic Provider",
        models=[],
        api=make_echo_api(),
        fetch_models=fetch_models,
    )
    print("[dynamic] 刷新前:", len(provider.get_models()))

    from nova_ai import RefreshModelsContext

    async def publish(publication):
        """演示用发布口：直接应用内存更新（真实场景由 Models 做世代校验与持久化）。"""
        if publication.update is not None:
            publication.update()
        return True

    asyncio.run(
        provider.refresh_models(
            RefreshModelsContext(publish=publish, allow_network=True)
        )
    )
    print("[dynamic] 刷新后:", [m.id for m in provider.get_models()])


if __name__ == "__main__":
    demo_builtin()
    asyncio.run(demo_custom())
    demo_dynamic()
