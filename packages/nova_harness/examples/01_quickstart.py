"""01 - nova_harness 最小会话

演示两条路径：
1. 离线 mock：经 ``CreateAgentSessionOptions.model_runtime`` 注入自定义模型
   运行时（``stream_simple`` 返回手写事件流），不依赖任何 API Key，
   完整跑通 prompt → 回复 → token 统计链路。
2. 真实调用：不传 ``model`` 与 ``model_runtime``，由 SDK 按初始模型解析链
   自动选择模型，用环境变量里的 key 发请求（未设置时自动跳过）。

两个模式都演示同一套核心流程：
``SessionManager.in_memory``（内存态会话，不落盘）→ ``create_agent_session``
→ ``session.prompt`` → ``get_last_assistant_text`` / ``get_session_stats``
→ ``session.dispose``。

运行：
    python examples/01_quickstart.py
    VOLCENGINE_API_KEY=<your-key> python examples/01_quickstart.py
"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from nova_ai import (
    AssistantMessage,
    DoneEvent,
    EventStream,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    Usage,
)

from nova_harness import CreateAgentSessionOptions, create_agent_session
from nova_harness.core.harness.session import SessionManager

# ----------------------------------------------------------------------
# 离线 mock：自定义模型运行时
# ----------------------------------------------------------------------
#
# CreateAgentSessionOptions 没有 stream_fn 字段——harness 的流式调用统一
# 由内部工厂构造，最终委托给 ``model_runtime.stream_simple(model, context,
# options)``。因此注入 mock 的官方入口就是 ``model_runtime``（接口契约见
# ``core/types/protocols.py`` 的 ``ModelRuntimeProtocol``）。
#
# 会话链路上真正会被调用的只有四个方法：
#   - ``refresh``：services 创建时无条件调用一次（动态模型目录刷新）；
#   - ``get_api_key``：``session.prompt`` 的鉴权前置检查；
#   - ``get_api_key_for_provider``：agent loop 发起请求前解析 key；
#   - ``stream_simple``：返回助手消息事件流（协议：可 ``async for`` 逐个
#     取事件，``await result()`` 取最终消息）。
# 其余方法按 protocol 补全为空实现即可。


def make_mock_model() -> Model:
    """构造一个仅用于离线演示的 Model 对象（provider/api 为自由字符串）。"""
    return Model(
        id="mock-model",
        name="Mock Model",
        api="mock",
        provider="mock",
        base_url="http://localhost/mock",
        reasoning=False,
        input_types=["text"],
        cost={"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
        context_window=128_000,
        max_tokens=8_192,
    )


def make_reply_stream(model: Model, text: str) -> EventStream:
    """手写一个助手消息事件流：start → 若干 text_delta → done。

    与真实协议实现同款形状（``nova_ai.EventStream``）：事件逐个 push，
    ``result()`` 在 done 事件后解出最终 ``AssistantMessage``。
    """
    stream = EventStream(
        is_complete=lambda e: e.type == "done",
        extract_result=lambda e: e.message,
    )
    partial = AssistantMessage(
        role="assistant",
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
    )
    stream.push(StartEvent(partial=partial))

    # 按词切片模拟流式增量；每个事件都携带"截至目前"的消息快照
    accumulated = ""
    for chunk in text.split(" "):
        accumulated = f"{accumulated} {chunk}".strip()
        partial = partial.model_copy(
            update={"content": [TextContent(text=accumulated)]}
        )
        stream.push(TextDeltaEvent(content_index=0, delta=chunk, partial=partial))

    final = partial.model_copy(
        update={
            "usage": Usage(input=18, output=6, total_tokens=24),
            "stop_reason": "stop",
        }
    )
    stream.push(DoneEvent(reason="stop", message=final))
    stream.end()
    return stream


class MockModelRuntime:
    """``ModelRuntimeProtocol`` 的最小实现：伪鉴权 + 固定文本的事件流。"""

    def __init__(self, model: Model, reply: str) -> None:
        self._model = model
        self._reply = reply

    # -- 会话链路真正调用的四个方法 --------------------------------------
    async def refresh(self, signal: Any = None) -> None:
        """真实实现会做网络刷新；mock 无事可做。"""

    async def get_api_key(self, model: Model) -> Optional[str]:
        return "mock-api-key"

    async def get_api_key_for_provider(self, provider: str) -> Optional[str]:
        return "mock-api-key"

    def stream_simple(self, model: Model, context: Any, options: Any = None) -> Any:
        return make_reply_stream(model, self._reply)

    # -- protocol 其余方法的空实现 ----------------------------------------
    async def get_request_auth(self, provider_or_model: Any) -> Optional[Any]:
        return None

    def is_using_oauth(self, provider_id: str) -> bool:
        return False

    def has_configured_auth(self, model: Model) -> bool:
        return True

    async def refresh_availability(self) -> None:
        return None

    async def login(self, provider_id: str, auth_type: Any, interaction: Any) -> Any:
        return None

    async def logout(self, provider_id: str) -> None:
        return None

    def register_provider(self, name: str, config: Any) -> None:
        return None

    def unregister_provider(self, name: str) -> None:
        return None

    def get_all(self) -> List[Model]:
        return [self._model]

    def get_available_snapshot(self) -> List[Model]:
        return [self._model]

    async def get_available(self, provider_id: Optional[str] = None) -> List[Model]:
        return [self._model]

    def find(self, provider: str, model_id: str) -> Optional[Model]:
        return self._model


# ----------------------------------------------------------------------
# 公共流程：一轮 prompt + 回复与 token 统计
# ----------------------------------------------------------------------
async def run_one_round(session: Any) -> None:
    """对任意来源的 session 跑一轮 prompt，打印回复与 token 用量。"""
    await session.prompt("用一句话介绍你自己")

    print("  助手回复:", session.get_last_assistant_text())

    stats = session.get_session_stats()
    tokens = stats.tokens
    print(
        "  token 用量: "
        f"input={tokens.input_tokens} output={tokens.output_tokens} "
        f"total={tokens.total}（消息数 {stats.total_messages}）"
    )


def make_options(workdir: Path, **overrides: Any) -> CreateAgentSessionOptions:
    """构造隔离的会话选项：cwd 与 agent_dir 都指向临时目录。

    这样不会读写 ``~/.nova/agent`` 下的任何配置（等效于把环境变量
    ``NOVA_AGENT_DIR`` 指到临时目录，只是这里走显式参数）；会话本身用
    内存态 SessionManager，跑完即弃、不落盘。
    """
    cwd = workdir / "ws"
    cwd.mkdir(parents=True, exist_ok=True)
    return CreateAgentSessionOptions(
        cwd=cwd,
        agent_dir=workdir / "agent",
        session_manager=SessionManager.in_memory(str(cwd)),
        **overrides,
    )


# ----------------------------------------------------------------------
# 1. 离线 mock：注入 model + model_runtime
# ----------------------------------------------------------------------
async def mock_demo() -> None:
    print("[mock] 离线演示：注入 MockModelRuntime，无需 API Key")
    with tempfile.TemporaryDirectory() as tmp:
        model = make_mock_model()
        options = make_options(
            Path(tmp),
            model=model,  # 显式指定初始模型，跳过模型解析链
            model_runtime=MockModelRuntime(model, reply="你好 我是 Nova 的 mock 助手"),
        )
        result = await create_agent_session(options)
        session = result.session
        print(f"  会话已创建: session_id={session.session_id}")

        await run_one_round(session)
        session.dispose()


# ----------------------------------------------------------------------
# 2. 真实调用：不传 model，走初始模型解析链（settings 默认 → 有鉴权模型）
# ----------------------------------------------------------------------
async def real_demo() -> None:
    if not os.environ.get("VOLCENGINE_API_KEY"):
        print("[real] VOLCENGINE_API_KEY 未设置，跳过真实调用")
        return

    print("[real] 真实调用：由 SDK 自动解析初始模型（Volcengine）")
    with tempfile.TemporaryDirectory() as tmp:
        result = await create_agent_session(make_options(Path(tmp)))
        session = result.session

        # 解析链未命中时的提示信息（本例有 key，通常为 None）
        if result.model_fallback_message:
            print("  模型解析提示:", result.model_fallback_message)
        model = session.model
        print(f"  解析得到模型: {model.provider}/{model.id}")

        await run_one_round(session)
        session.dispose()


if __name__ == "__main__":
    asyncio.run(mock_demo())
    asyncio.run(real_demo())
