"""EventController 的 message_end 转发回归测试。

回归：controller 曾把**裸消息**传给 ``runner.emit_message_end``（其签名收
``MessageEndEvent`` 事件对象），任何真实运行在首个 message_end 即崩
（``'UserMessage' object has no attribute 'message'``）——print/RPC 全线。
测试替身覆盖 controller 触碰面，runner 用真实 ExtensionRunner。
"""

from types import SimpleNamespace
from typing import Any, List

import pytest
from nova_protocol import TextContent, UserMessage

from nova_harness.core.agent_session.controllers.events import EventController
from nova_harness.events.results import MessageEndEventResult
from nova_harness.extensions.runner import ExtensionRunner
from nova_harness.extensions.runtime import ExtensionRuntime


class _FakeQueue:
    def emit_update(self) -> None:
        pass


class _FakeSessionManager:
    def __init__(self) -> None:
        self.appended: List[Any] = []

    def append_message(self, message: Any) -> None:
        self.appended.append(message)


def _make_session(runner: Any) -> Any:
    from unittest.mock import MagicMock

    session = MagicMock()
    session._extension_runner = runner
    session._steering_messages = []
    session._follow_up_messages = []
    session._queue = MagicMock()
    session._overflow_recovery_attempted = False
    session._retry_attempt = 0
    session._last_assistant_message = None
    session.session_manager = MagicMock()
    session.session_manager.appended = []
    session.session_manager.append_message.side_effect = (
        session.session_manager.appended.append
    )
    session.emitted = []
    session._emit = lambda event: session.emitted.append(event)
    return session


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
    session = _make_session(_make_runner([]))
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
    session = _make_session(_make_runner([fake_extension]))
    controller = EventController(session)
    message = _user_message("原始")

    await controller.handle(SimpleNamespace(type="message_end", message=message))

    # 原地替换：同一对象的内容被改写（agent state 与持久化引用一致）
    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "改写后"
