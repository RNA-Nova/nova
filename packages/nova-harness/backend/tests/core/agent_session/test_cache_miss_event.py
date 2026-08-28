"""cache_miss 事件发射测试（pi maybeShowCacheMissNotice 对位）。

链路：message_end（assistant 正常结束）→ settings 门控 → detect_cache_miss
（持久化前，entries 不含当前消息）→ CacheMissEvent 上 Bus 2。
"""

from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from nova_ai import AssistantMessage, Cost, TextContent, Usage

from nova_harness.core.agent_session.controllers.events import EventController
from nova_harness.core.types.events import CacheMissEvent


class _FakeQueue:
    def emit_update(self) -> None:
        pass


class _FakeSessionManager:
    def __init__(self, entries: Optional[List[Any]] = None) -> None:
        self._entries = entries or []
        self.appended: List[Any] = []

    def get_entries(self) -> List[Any]:
        return list(self._entries)

    def append_message(self, message: Any) -> None:
        self.appended.append(message)


class _FakeSettingsManager:
    def __init__(self, show_notices: bool) -> None:
        self._settings = SimpleNamespace(show_cache_miss_notices=show_notices)

    def get_settings(self) -> Any:
        return self._settings


class _FakeSession:
    def __init__(self, show_notices: bool, entries: Optional[List[Any]] = None) -> None:
        self._extension_runner = None
        self._steering_messages: List[str] = []
        self._follow_up_messages: List[str] = []
        self._queue = _FakeQueue()
        self._overflow_recovery_attempted = False
        self._retry_attempt = 0
        self._last_assistant_message = None
        self.session_manager = _FakeSessionManager(entries)
        self.settings_manager = _FakeSettingsManager(show_notices)
        self.model_runtime = SimpleNamespace(find=lambda provider, model_id: None)
        self.emitted: List[Any] = []

    def _emit(self, event: Any) -> None:
        self.emitted.append(event)


def _assistant_message(
    *,
    input: int,
    cache_read: int,
    cache_write: int,
    timestamp: int,
    cost_input: float = 0.1,
) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="ok")],
        provider="volcengine",
        model="deepseek-v3",
        usage=Usage(
            input=input,
            cache_read=cache_read,
            cache_write=cache_write,
            cost=Cost(input=cost_input, cache_read=0.0, cache_write=0.0),
        ),
        timestamp=timestamp,
        stop_reason="stop",
    )


def _entry(message: AssistantMessage) -> SimpleNamespace:
    return SimpleNamespace(type="message", message=message)


@pytest.mark.asyncio
async def test_cache_miss_emitted_when_significant():
    """上轮报告缓存活动、本轮全量 miss 且超噪声地板 → 发射 CacheMissEvent。"""
    prev = _assistant_message(
        input=20000, cache_read=0, cache_write=30000, timestamp=1000
    )
    current = _assistant_message(
        input=45000, cache_read=0, cache_write=0, timestamp=2000
    )
    session = _FakeSession(show_notices=True, entries=[_entry(prev)])
    controller = EventController(session)

    await controller.handle(SimpleNamespace(type="message_end", message=current))

    misses = [e for e in session.emitted if isinstance(e, CacheMissEvent)]
    assert len(misses) == 1
    assert misses[0].missed_tokens == 45000  # min(50000, 45000) - 0
    assert misses[0].missed_cost > 0
    assert misses[0].idle_ms == 1000
    assert misses[0].model_changed is False


@pytest.mark.asyncio
async def test_cache_miss_suppressed_when_setting_off():
    """settings 门控关闭：同样的 miss 场景不发射。"""
    prev = _assistant_message(
        input=20000, cache_read=0, cache_write=30000, timestamp=1000
    )
    current = _assistant_message(
        input=45000, cache_read=0, cache_write=0, timestamp=2000
    )
    session = _FakeSession(show_notices=False, entries=[_entry(prev)])
    controller = EventController(session)

    await controller.handle(SimpleNamespace(type="message_end", message=current))

    assert not [e for e in session.emitted if isinstance(e, CacheMissEvent)]


@pytest.mark.asyncio
async def test_cache_miss_not_emitted_on_noise_floor_or_first_turn():
    """低于噪声地板 / 首轮（无 prev）不发射。"""
    # 低于噪声地板（miss <= 1024）
    prev = _assistant_message(input=600, cache_read=0, cache_write=500, timestamp=1000)
    current = _assistant_message(
        input=1000, cache_read=100, cache_write=0, timestamp=2000
    )
    session = _FakeSession(show_notices=True, entries=[_entry(prev)])
    controller = EventController(session)
    await controller.handle(SimpleNamespace(type="message_end", message=current))
    assert not [e for e in session.emitted if isinstance(e, CacheMissEvent)]

    # 首轮（无 prev）
    session2 = _FakeSession(show_notices=True, entries=[])
    controller2 = EventController(session2)
    await controller2.handle(
        SimpleNamespace(
            type="message_end",
            message=_assistant_message(
                input=50000, cache_read=0, cache_write=0, timestamp=1000
            ),
        )
    )
    assert not [e for e in session2.emitted if isinstance(e, CacheMissEvent)]
