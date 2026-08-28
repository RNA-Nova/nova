"""01 - nova_ai 最小用法

演示两条路径：
1. 离线 mock：手写一个"协议模块"（两个函数即满足 ProviderStreams 协议），
   不依赖任何 API Key，可直接运行。
2. 真实调用：`builtin_models()` 拿到内置 Models，用环境变量里的 key 发请求。

运行：
    python examples/01_quickstart.py
"""

import asyncio
import os

from nova_ai import (
    AssistantMessage,
    Context,
    DoneEvent,
    EventStream,
    StartEvent,
    TextContent,
    UserMessage,
)


# ----------------------------------------------------------------------
# 1. 离线 mock：协议模块就是一个带 stream/stream_simple 函数的对象
# ----------------------------------------------------------------------
def _mock_stream(model, context, options=None):
    """最小协议实现：忽略请求，返回固定文本流。"""
    stream = EventStream(
        is_complete=lambda e: e.type == "done",
        extract_result=lambda e: e.message,
    )
    partial = AssistantMessage(
        role="assistant",
        content=[TextContent(text="hello from mock")],
        api="mock",
        provider="mock",
        model="mock",
        stop_reason="stop",
    )
    stream.push(StartEvent(partial=partial))
    stream.push(DoneEvent(reason="stop", message=partial))
    stream.end()
    return stream


def mock_demo():
    stream = _mock_stream(None, None)
    message = asyncio.run(stream.result())
    print("[mock]", message.content[0].text)


# ----------------------------------------------------------------------
# 2. 真实调用：builtin_models() + 环境变量 key
# ----------------------------------------------------------------------
async def real_demo():
    from nova_ai import builtin_models, get_volcengine_model

    if not os.environ.get("VOLCENGINE_API_KEY"):
        print("[real] VOLCENGINE_API_KEY 未设置，跳过真实调用")
        return

    models = builtin_models()
    model = get_volcengine_model("deepseek-v3-2-251201")
    context = Context(messages=[UserMessage(role="user", content="用一句话介绍你自己")])
    message = await models.stream_simple(model, context).result()
    print("[real]", message.content[0].text)


if __name__ == "__main__":
    mock_demo()
    asyncio.run(real_demo())
