"""EventController 的系统消费与扩展工作队列契约。"""

import asyncio
from types import SimpleNamespace
from typing import Any, List

import pytest
from nova_async import TaskTracker
from nova_protocol import TextContent, UserMessage

from nova_harness.core.agent_session.controllers.events import EventController
from nova_harness.events import AgentEndEvent
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
    session._tasks = TaskTracker(name="test-extension-events")
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
    """零扩展 runner：message_end 正常穿透并完成持久化。"""
    session = _make_session(_make_runner([]))
    controller = EventController(session)
    message = _user_message("hi")

    await controller.handle(SimpleNamespace(type="message_end", message=message))
    await controller.aclose()

    assert session.session_manager.appended == [message]
    assert len(session.emitted) == 1


@pytest.mark.asyncio
async def test_message_end_extension_rewrite_applied_in_place():
    """干预型改写完成后才持久化，且保持同一消息对象。"""

    def rewrite_handler(event: Any, ctx: Any) -> MessageEndEventResult:
        return MessageEndEventResult(message=_user_message("改写后"))

    fake_extension = SimpleNamespace(
        path="/tmp/fake-ext",
        handlers={"message_end": [rewrite_handler]},
    )
    session = _make_session(_make_runner([fake_extension]))
    controller = EventController(session)
    message = _user_message("原始")

    await controller.on_message_end(message)
    await controller.handle(SimpleNamespace(type="message_end", message=message))
    await controller.aclose()

    assert isinstance(message.content[0], TextContent)
    assert message.content[0].text == "改写后"
    assert session.session_manager.appended == [message]


@pytest.mark.asyncio
async def test_agent_end_is_emitted_through_system_channel():
    session = _make_session(_make_runner([]))
    session._current_run_id = "run-1"
    session._retry.will_retry_after_agent_end.return_value = False
    controller = EventController(session)
    event = SimpleNamespace(type="agent_end", messages=[])

    await controller.handle(event)
    await controller.aclose()

    assert len(session.emitted) == 1
    assert isinstance(session.emitted[0], AgentEndEvent)
    assert session.emitted[0].run_id == "run-1"
    assert session._current_run_id is None


@pytest.mark.asyncio
async def test_slow_observation_does_not_block_agent_end():
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_handler(event: Any, ctx: Any) -> None:
        started.set()
        await release.wait()

    fake_extension = SimpleNamespace(
        path="/tmp/fake-ext",
        handlers={"agent_end": [slow_handler]},
    )
    session = _make_session(_make_runner([fake_extension]))
    session._current_run_id = "run-1"
    session._retry.will_retry_after_agent_end.return_value = False
    controller = EventController(session)

    await controller.handle(SimpleNamespace(type="agent_end", messages=[]))
    await started.wait()
    # 扩展仍在等待，但系统终态已经发布，run 身份也已清理。
    assert len(session.emitted) == 1
    assert isinstance(session.emitted[0], AgentEndEvent)
    assert session._current_run_id is None

    release.set()
    await controller.aclose()
