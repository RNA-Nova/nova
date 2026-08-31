"""EventController 的 message_end 转发回归测试。

回归：controller 曾把**裸消息**传给 ``runner.emit_message_end``（其签名收
``MessageEndEvent`` 事件对象），任何真实运行在首个 message_end 即崩
（``'UserMessage' object has no attribute 'message'``）——print/RPC 全线。
测试替身覆盖 controller 触碰面，runner 用真实 ExtensionRunner。
"""

from types import SimpleNamespace
from typing import Any, List

import pytest
from nova_ai import TextContent, UserMessage

from nova_harness.core.agent_session.controllers.events import EventController
from nova_harness.core.extensions.runner import ExtensionRunner
from nova_harness.core.types.events.results import MessageEndEventResult
from nova_harness.core.types.extensions.runtime import ExtensionRuntime


class _FakeQueue:
    def emit_update(self) -> None:
        pass


class _FakeSessionManager:
    def __init__(self) -> None:
        self.appended: List[Any] = []

    def append_message(self, message: Any) -> None:
        self.appended.append(message)


class _FakeSession:
    """EventController 触碰面的最小替身。"""

    def __init__(self, runner: Any) -> None:
        self._extension_runner = runner
        self._steering_messages: List[str] = []
        self._follow_up_messages: List[str] = []
        self._queue = _FakeQueue()
        self._overflow_recovery_attempted = False
        self._retry_attempt = 0
        self._last_assistant_message = None
        self.session_manager = _FakeSessionManager()
        self.emitted: List[Any] = []

    def _emit(self, event: Any) -> None:
        self.emitted.append(event)


def _make_runner(extensions: List[Any]) -> ExtensionRunner:
    return ExtensionRunner(
        extensions=extensions,
        runtime=ExtensionRuntime(),
        cwd="/tmp",
        session_manager=None,
    )


def _user_message(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


@pytest.mark.asyncio
async def test_message_end_forwarding_wraps_event():
    """零扩展 runner：message_end 正常穿透（回归：裸消息传参会 AttributeError）。"""
    session = _FakeSession(_make_runner([]))
    controller = EventController(session)
    message = _user_message("hi")

    await controller.handle(SimpleNamespace(type="message_end", message=message))

    # 消息被持久化、事件被透传——完整走完 handle 全程无异常
    assert session.session_manager.appended == [message]
    assert len(session.emitted) == 1


@pytest.mark.asyncio
async def test_message_end_extension_rewrite_applied_in_place():
    """扩展改写 message_end：替换内容原地生效（role 不变校验通过）。"""

    def rewrite_handler(event: Any, ctx: Any) -> MessageEndEventResult:
        return MessageEndEventResult(message=_user_message("改写后"))

    fake_extension = SimpleNamespace(
        path="/tmp/fake-ext",
        handlers={"message_end": [rewrite_handler]},
    )
    session = _FakeSession(_make_runner([fake_extension]))
    controller = EventController(session)
    message = _user_message("原始")

    await controller.handle(SimpleNamespace(type="message_end", message=message))

    # 原地替换：同一对象的内容被改写（agent state 与持久化引用一致）
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "改写后"
