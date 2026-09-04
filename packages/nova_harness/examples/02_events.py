"""02 - 事件流订阅：session.subscribe()

``session.subscribe(listener)`` 注册一个监听器，收到会话运行期的全部事件，
返回值是退订函数。事件分两层：

- 底层 Agent 生命周期事件（原样转发）：
  ``agent_start`` / ``agent_end`` —— 一轮运行的边界；
  ``turn_start`` / ``turn_end`` —— 一个 turn（一次"模型请求-响应"，含工具
  调用循环，无工具时一轮 prompt 通常只有一个 turn）；
  ``message_start`` / ``message_update`` / ``message_end`` —— 消息生命周期
  （user 与 assistant 消息都有；``message_update`` 携带流式增量）；
  ``tool_execution_start`` / ``tool_execution_update`` / ``tool_execution_end``
  —— 工具执行（本示例会话未挂工具，不会出现）。
- AgentSession 运行时事件：``agent_settled``（run 终结后发射）、
  ``model_changed``、``queue_update``、``auto_retry_start`` / ``auto_retry_end``、
  ``auto_compaction_start`` / ``auto_compaction_end`` 等。

离线 mock 模式全程可跑（注入自定义 model_runtime，原理见 01_quickstart.py）；
设置 VOLCENGINE_API_KEY 后再演示真实模型的事件序列。

运行：
    python examples/02_events.py
    VOLCENGINE_API_KEY=<your-key> python examples/02_events.py
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
)

from nova_harness import CreateAgentSessionOptions, create_agent_session
from nova_harness.core.harness.session import SessionManager


# ----------------------------------------------------------------------
# 离线 mock（与 01 同款注入点：CreateAgentSessionOptions.model_runtime）
# ----------------------------------------------------------------------
def make_mock_model() -> Model:
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


def make_reply_stream(model: Model, chunks: List[str]) -> EventStream:
    """逐块推送 text_delta 的事件流——每个 delta 触发一次 message_update。"""
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
    accumulated = ""
    for chunk in chunks:
        accumulated += chunk
        partial = partial.model_copy(
            update={"content": [TextContent(text=accumulated)]}
        )
        stream.push(TextDeltaEvent(content_index=0, delta=chunk, partial=partial))
    stream.push(DoneEvent(reason="stop", message=partial))
    stream.end()
    return stream


class MockModelRuntime:
    """``ModelRuntimeProtocol`` 的最小实现（会话链路只调其中四个方法）。"""

    def __init__(self, model: Model, chunks: List[str]) -> None:
        self._model = model
        self._chunks = chunks

    async def refresh(self, signal: Any = None) -> None:
        """真实实现会做网络刷新；mock 无事可做。"""

    async def get_api_key(self, model: Model) -> Optional[str]:
        return "mock-api-key"

    async def get_api_key_for_provider(self, provider: str) -> Optional[str]:
        return "mock-api-key"

    def stream_simple(self, model: Model, context: Any, options: Any = None) -> Any:
        return make_reply_stream(model, self._chunks)

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
# 事件监听器：打印事件类型，并附上有助于理解的负载摘要
# ----------------------------------------------------------------------
def describe(event: Any) -> str:
    """把事件渲染成一行摘要：类型 + 关键负载。"""
    event_type = event.type
    message = getattr(event, "message", None)
    role = getattr(message, "role", None)

    if event_type == "message_update":
        # message_update 携带底层流式事件（text_delta / thinking_delta / ...）
        stream_event = getattr(event, "assistant_message_event", None)
        delta_type = getattr(stream_event, "type", "?")
        return f"{event_type} (assistant, {delta_type})"
    if role is not None:
        return f"{event_type} ({role})"
    return event_type


async def run_with_subscription(session: Any) -> List[str]:
    """订阅事件并跑一轮 prompt；返回收集到的事件摘要序列。"""
    collected: List[str] = []

    def listener(event: Any) -> None:
        summary = describe(event)
        collected.append(summary)
        print(f"  event: {summary}")

    unsubscribe = session.subscribe(listener)
    try:
        await session.prompt("用一句话介绍你自己")
    finally:
        unsubscribe()  # 退订后不再收到事件
    return collected


def make_options(workdir: Path, **overrides: Any) -> CreateAgentSessionOptions:
    """隔离选项：cwd / agent_dir 指向临时目录（不碰 ~/.nova/agent），
    会话内存态不落盘。"""
    cwd = workdir / "ws"
    cwd.mkdir(parents=True, exist_ok=True)
    return CreateAgentSessionOptions(
        cwd=cwd,
        agent_dir=workdir / "agent",
        session_manager=SessionManager.in_memory(str(cwd)),
        **overrides,
    )


# ----------------------------------------------------------------------
# 1. 离线 mock：text_delta 流触发连续的 message_update
# ----------------------------------------------------------------------
async def mock_demo() -> None:
    print("[mock] 离线演示：一轮 prompt 的完整事件序列")
    with tempfile.TemporaryDirectory() as tmp:
        model = make_mock_model()
        options = make_options(
            Path(tmp),
            model=model,
            model_runtime=MockModelRuntime(
                model, chunks=["你好，", "我是 Nova", " 的 mock 助手。"]
            ),
        )
        result = await create_agent_session(options)
        session = result.session

        collected = await run_with_subscription(session)

        # 退订后再发事件也收不到——这里用会话统计侧面验证状态已就绪
        print(
            f"  共收集 {len(collected)} 个事件；最终回复: {session.get_last_assistant_text()}"
        )
        session.dispose()


# ----------------------------------------------------------------------
# 2. 真实调用：真实模型流的事件序列（message_update 次数取决于流式分片）
# ----------------------------------------------------------------------
async def real_demo() -> None:
    if not os.environ.get("VOLCENGINE_API_KEY"):
        print("[real] VOLCENGINE_API_KEY 未设置，跳过真实调用")
        return

    print("[real] 真实调用：观察真实模型流的事件序列")
    with tempfile.TemporaryDirectory() as tmp:
        result = await create_agent_session(make_options(Path(tmp)))
        session = result.session

        collected = await run_with_subscription(session)

        updates = sum(1 for e in collected if e.startswith("message_update"))
        print(f"  共收集 {len(collected)} 个事件，其中 message_update × {updates}")
        session.dispose()


if __name__ == "__main__":
    asyncio.run(mock_demo())
    asyncio.run(real_demo())
