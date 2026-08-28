"""user_bash 扩展事件接线测试（UserToolController.invoke 路径）。

对齐 pi ``handleBashCommand`` 语义：bash 用户工具执行前发射
``user_bash`` 事件——扩展返回完整 result 时跳过真实执行直接记录
其返回；返回 operations 时注入 ``params["operations"]`` 走原路径；
无扩展 / 无 handler / 非 bash 工具时原路径完全不变。
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional

import pytest
from nova_agent import CustomAgentMessage

from nova_harness.core.agent_session.controllers.user_tools import UserToolController
from nova_harness.core.types.events import USER_BASH, UserBashEventResult
from nova_harness.core.types.resources.user_tools import UserToolDefinition


class _FakeBashMessage(CustomAgentMessage):
    """占位消息：模拟 bash 用户工具产出的 BashExecutionMessage。"""

    text: str = ""
    role: Literal["fakeBash"] = "fakeBash"


class _FakeRunner:
    """最小 ExtensionRunner 替身：按预设返回 user_bash 事件结果。"""

    def __init__(
        self,
        event_result: Optional[UserBashEventResult] = None,
        has_handler: bool = True,
    ) -> None:
        self._event_result = event_result
        self._has_handler = has_handler
        self.emitted_events: List[Any] = []
        self.errors: List[Any] = []

    def has_handlers(self, event_type: str) -> bool:
        return event_type == USER_BASH and self._has_handler

    async def emit_user_bash(self, event: Any) -> Optional[UserBashEventResult]:
        self.emitted_events.append(event)
        return self._event_result

    def emit_error(self, error: Any) -> None:
        self.errors.append(error)


class _FakeManager:
    """最小 UserToolManager 替身：记录 invoke 调用并返回固定消息。"""

    def __init__(self, definition: Optional[UserToolDefinition] = None) -> None:
        self._definition = definition
        self.invoke_calls: List[Dict[str, Any]] = []

    def get(self, name: str) -> Optional[UserToolDefinition]:
        return self._definition

    async def invoke(
        self, name: str, params: Dict[str, Any], on_event: Any, signal: Any
    ) -> CustomAgentMessage:
        self.invoke_calls.append({"name": name, "params": params})
        return _FakeBashMessage(text=f"executed:{params.get('command', '')}")


def _make_session(runner: Any, cwd: str = "/tmp/work") -> Any:
    """非流式会话替身：record 双写 agent.state.messages + session_manager。"""
    return SimpleNamespace(
        extension_runner=runner,
        is_streaming=False,
        _pending_session_messages=[],
        _emit=lambda event: None,
        agent=SimpleNamespace(state=SimpleNamespace(messages=[])),
        session_manager=SimpleNamespace(
            get_cwd=lambda: cwd,
            append_message=lambda message: None,
        ),
    )


def _bash_definition(
    build_result_message: Any = None,
) -> UserToolDefinition:
    return UserToolDefinition(
        name="bash",
        description="bash user tool",
        build_result_message=build_result_message,
    )


@pytest.mark.asyncio
async def test_extension_result_replaces_execution():
    """扩展返回完整 result：跳过真实执行，直接记录转换后的消息。"""
    built_with: List[Any] = []

    def _builder(params: Dict[str, Any], result: Any) -> CustomAgentMessage:
        built_with.append({"params": params, "result": result})
        return _FakeBashMessage(text=f"built:{result['output']}")

    runner = _FakeRunner(
        UserBashEventResult(result={"output": "remote-out", "exit_code": 0})
    )
    manager = _FakeManager(_bash_definition(build_result_message=_builder))
    session = _make_session(runner)
    controller = UserToolController(session, manager)

    message = await controller.invoke(
        "bash", {"command": "ls", "exclude_from_context": True}
    )

    # 事件载荷对齐 pi 契约：command / exclude_from_context / cwd
    assert len(runner.emitted_events) == 1
    event = runner.emitted_events[0]
    assert event.type == USER_BASH
    assert event.command == "ls"
    assert event.exclude_from_context is True
    assert event.cwd == "/tmp/work"
    # 真实执行被跳过：manager.invoke 未被调用
    assert manager.invoke_calls == []
    # 扩展 result 经工具转换器翻译为消息并记录进会话
    assert built_with[0]["result"] == {"output": "remote-out", "exit_code": 0}
    assert message.text == "built:remote-out"
    assert session.agent.state.messages == [message]


@pytest.mark.asyncio
async def test_extension_operations_injected_into_params():
    """扩展返回 operations：注入 params 走原执行路径，不污染调用方字典。"""
    fake_operations = SimpleNamespace(kind="remote")
    runner = _FakeRunner(UserBashEventResult(operations=fake_operations))
    manager = _FakeManager(_bash_definition())
    session = _make_session(runner)
    controller = UserToolController(session, manager)

    caller_params = {"command": "echo hi"}
    message = await controller.invoke("bash", caller_params)

    # 走原执行路径，且 operations 已注入工具执行参数
    assert len(manager.invoke_calls) == 1
    assert manager.invoke_calls[0]["params"]["operations"] is fake_operations
    assert manager.invoke_calls[0]["params"]["command"] == "echo hi"
    assert message.text == "executed:echo hi"
    assert session.agent.state.messages == [message]
    # 注入不污染调用方传入的字典
    assert "operations" not in caller_params


@pytest.mark.asyncio
async def test_no_extension_runner_unchanged():
    """无扩展 runner：不发射事件，原路径完全不变。"""
    manager = _FakeManager(_bash_definition())
    session = _make_session(runner=None)
    controller = UserToolController(session, manager)

    message = await controller.invoke("bash", {"command": "pwd"})

    assert len(manager.invoke_calls) == 1
    assert manager.invoke_calls[0]["params"] == {"command": "pwd"}
    assert message.text == "executed:pwd"
    assert session.agent.state.messages == [message]


@pytest.mark.asyncio
async def test_no_user_bash_handler_unchanged():
    """runner 无 user_bash handler：不发射事件（零开销短路），原路径不变。"""
    runner = _FakeRunner(has_handler=False)
    manager = _FakeManager(_bash_definition())
    session = _make_session(runner)
    controller = UserToolController(session, manager)

    message = await controller.invoke("bash", {"command": "pwd"})

    assert runner.emitted_events == []
    assert len(manager.invoke_calls) == 1
    assert message.text == "executed:pwd"


@pytest.mark.asyncio
async def test_handler_returning_none_unchanged():
    """handler 无返回（None）：不拦截，原路径不变。"""
    runner = _FakeRunner(event_result=None)
    manager = _FakeManager(_bash_definition())
    session = _make_session(runner)
    controller = UserToolController(session, manager)

    message = await controller.invoke("bash", {"command": "pwd"})

    assert len(runner.emitted_events) == 1
    assert len(manager.invoke_calls) == 1
    assert message.text == "executed:pwd"


@pytest.mark.asyncio
async def test_non_bash_tool_not_intercepted():
    """非 bash 用户工具：不发射 user_bash 事件，原路径不变。"""
    runner = _FakeRunner(UserBashEventResult(result={"output": "x"}))
    manager = _FakeManager(
        UserToolDefinition(name="fake", description="fake user tool")
    )
    session = _make_session(runner)
    controller = UserToolController(session, manager)

    message = await controller.invoke("fake", {"command": "pwd"})

    assert runner.emitted_events == []
    assert len(manager.invoke_calls) == 1
    assert manager.invoke_calls[0]["name"] == "fake"
    assert message.text == "executed:pwd"


@pytest.mark.asyncio
async def test_result_without_builder_falls_back_with_error():
    """防御分支：扩展返回 result 但工具未声明转换器——报异常并走原路径。"""
    runner = _FakeRunner(UserBashEventResult(result={"output": "remote-out"}))
    manager = _FakeManager(_bash_definition(build_result_message=None))
    session = _make_session(runner)
    controller = UserToolController(session, manager)

    message = await controller.invoke("bash", {"command": "ls"})

    assert len(runner.errors) == 1
    assert runner.errors[0]["event"] == USER_BASH
    assert len(manager.invoke_calls) == 1
    assert message.text == "executed:ls"


# ---------------------------------------------------------------------------
# record/flush_pending 的消息事件发射（mirror 完结定稿依赖）
# ---------------------------------------------------------------------------


def test_record_emits_message_events_when_not_streaming():
    """非流式 record：双写之外发射 MessageStart/MessageEnd（前端卡片定稿事件）。"""
    from nova_harness.core.types.events.agent import MessageEndEvent, MessageStartEvent

    emitted: List[Any] = []
    session = _make_session(_FakeRunner())
    session._emit = lambda event: emitted.append(event)
    controller = UserToolController(session, _FakeManager())

    message = _FakeBashMessage(text="done")
    controller.record(message)

    assert len(emitted) == 2
    assert isinstance(emitted[0], MessageStartEvent)
    assert isinstance(emitted[1], MessageEndEvent)
    assert emitted[0].message is message
    assert emitted[1].message is message


def test_record_pending_when_streaming_then_flush_emits():
    """流式期间挂起不发事件；flush 时逐条双写并发射定稿事件。"""
    from nova_harness.core.types.events.agent import MessageEndEvent, MessageStartEvent

    emitted: List[Any] = []
    session = _make_session(_FakeRunner())
    session.is_streaming = True
    session._emit = lambda event: emitted.append(event)
    controller = UserToolController(session, _FakeManager())

    message = _FakeBashMessage(text="during-run")
    controller.record(message)
    assert emitted == []  # 流式挂起，不发事件
    assert session._pending_session_messages == [message]

    controller.flush_pending()
    assert session._pending_session_messages == []
    assert [type(e) for e in emitted] == [MessageStartEvent, MessageEndEvent]
    assert session.agent.state.messages == [message]
