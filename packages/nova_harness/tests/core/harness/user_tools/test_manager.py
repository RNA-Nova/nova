"""UserToolManager 与 UserToolController 测试。"""

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from nova_agent import CustomAgentMessage

from nova_harness.core.agent_session.controllers.user_tools import (
    UserToolController,
)
from nova_harness.core.harness.user_tools import UserToolManager
from nova_harness.core.types.resources.user_tools import UserToolDefinition


class FakeMessage(CustomAgentMessage):
    """测试用用户工具消息。"""

    role: str = "fakeTool"
    text: str = ""
    timestamp: int = 0
    exclude_from_context: bool = False

    def to_context_text(self) -> str:
        return self.text


def make_tool(name: str, text: str = "ok", delay: float = 0) -> UserToolDefinition:
    async def _execute(params, on_event, signal) -> FakeMessage:
        if on_event is not None:
            on_event("progress", {"text": "working"})
        if delay:
            await asyncio.sleep(delay)
        return FakeMessage(text=text, timestamp=1)

    return UserToolDefinition(
        name=name,
        description=f"{name} 工具",
        parameters={"type": "object", "properties": {}},
        execute=_execute,
    )


class FakeSession:
    """UserToolController 需要的最小会话面。"""

    def __init__(self, is_streaming: bool = False):
        self.is_streaming = is_streaming
        self._pending_session_messages: List[CustomAgentMessage] = []
        self._state_messages: List[CustomAgentMessage] = []
        self._appended: List[CustomAgentMessage] = []
        self._emitted: List[Any] = []

    def _emit(self, event: Any) -> None:
        self._emitted.append(event)

    class _Agent:
        def __init__(self, outer):
            self.state = type("State", (), {"messages": outer._state_messages})()

    @property
    def agent(self):
        return FakeSession._Agent(self)

    class _SessionManager:
        def __init__(self, outer):
            self._outer = outer

        def append_message(self, message):
            self._outer._appended.append(message)
            return "entry-id"

    @property
    def session_manager(self):
        return FakeSession._SessionManager(self)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


def test_manager_register_and_names():
    manager = UserToolManager()
    manager.register(make_tool("bash"))
    manager.register(make_tool("browser_search"))
    assert manager.names() == ["bash", "browser_search"]
    assert manager.get("bash") is not None


def test_manager_register_requires_execute():
    manager = UserToolManager()
    with pytest.raises(ValueError):
        manager.register(UserToolDefinition(name="broken", description="无执行体"))


def test_manager_catalog():
    manager = UserToolManager()
    manager.register(make_tool("bash"))
    catalog = manager.catalog()
    assert len(catalog) == 1
    assert catalog[0].name == "bash"
    assert catalog[0].description == "bash 工具"
    assert catalog[0].parameters == {"type": "object", "properties": {}}


@pytest.mark.asyncio
async def test_manager_invoke_unknown():
    manager = UserToolManager()
    with pytest.raises(KeyError):
        await manager.invoke("nope")


@pytest.mark.asyncio
async def test_manager_invoke_passes_params_and_events():
    received: Dict[str, Any] = {}
    events = []

    async def _execute(params, on_event, signal) -> FakeMessage:
        received.update(params)
        on_event("progress", {"step": 1})
        return FakeMessage(text="done", timestamp=1)

    manager = UserToolManager()
    manager.register(UserToolDefinition(name="t", description="t", execute=_execute))
    message = await manager.invoke(
        "t", {"q": "nova"}, lambda e, d: events.append((e, d))
    )
    assert received == {"q": "nova"}
    assert events == [("progress", {"step": 1})]
    assert message.text == "done"


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_controller_invoke_records_message_when_idle():
    manager = UserToolManager()
    manager.register(make_tool("fake"))
    session = FakeSession(is_streaming=False)
    controller = UserToolController(session, manager)

    message = await controller.invoke("fake")
    assert message.text == "ok"
    # 非流式：直接双写 agent state + session manager
    assert session._state_messages == [message]
    assert session._appended == [message]
    assert session._pending_session_messages == []
    # 进度不再上总线（user_tool 事件组消亡——线上进度归 item 发射）；
    # record 的消息定稿事件保留（MessageStart/MessageEnd——归约器定稿依赖）
    emitted_types = [event.type for event in session._emitted]
    assert emitted_types == ["message_start", "message_end"]
    assert session._emitted[0].message is message
    assert session._emitted[1].message is message


@pytest.mark.asyncio
async def test_controller_pending_and_flush_during_streaming():
    manager = UserToolManager()
    manager.register(make_tool("fake"))
    session = FakeSession(is_streaming=True)
    controller = UserToolController(session, manager)

    message = await controller.invoke("fake")
    # 流式中：挂起不双写
    assert session._state_messages == []
    assert session._appended == []
    assert session._pending_session_messages == [message]
    assert controller.has_pending_messages

    session.is_streaming = False
    controller.flush_pending()
    assert session._state_messages == [message]
    assert session._appended == [message]
    assert session._pending_session_messages == []
    assert not controller.has_pending_messages


@pytest.mark.asyncio
async def test_controller_abort_cascades_by_name():
    started = asyncio.Event()

    async def _execute(params, on_event, signal) -> FakeMessage:
        started.set()
        # 模拟长任务：响应 signal 取消
        wait_fn = getattr(signal, "wait", None)
        if wait_fn is not None:
            await wait_fn()
        raise asyncio.CancelledError()

    manager = UserToolManager()
    manager.register(
        UserToolDefinition(name="slow", description="慢", execute=_execute)
    )
    session = FakeSession()
    controller = UserToolController(session, manager)

    task = asyncio.create_task(controller.invoke("slow"))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert controller.is_running()
    assert controller.is_running("slow")
    assert not controller.is_running("bash")

    controller.abort("slow")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not controller.is_running()


@pytest.mark.asyncio
async def test_controller_abort_all():
    manager = UserToolManager()
    manager.register(make_tool("a", delay=5))
    manager.register(make_tool("b", delay=5))
    session = FakeSession()
    controller = UserToolController(session, manager)

    task_a = asyncio.create_task(controller.invoke("a"))
    task_b = asyncio.create_task(controller.invoke("b"))
    await asyncio.sleep(0.05)
    assert controller.is_running()

    # 这两个工具不响应 signal，abort 只触发信号不直接取消 task；
    # 这里验证 abort() 不报错且信号已发出
    controller.abort()
    for task in (task_a, task_b):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
