"""``_shared/timeouts.py`` 测试（超时常量语义 + SSE idle 看门狗）。"""

import asyncio

import httpx
import httpx2
import pytest
from nova_protocol import NovaErrorKind

from nova_ai.api_impls._shared.timeouts import (
    CONNECT_TIMEOUT_S,
    DEFAULT_STREAM_TIMEOUT_S,
    build_request_timeout,
    build_request_timeout_httpx2,
    idle_guarded,
    idle_timeout_of,
)
from nova_ai.utils.errors import RequestTimeoutError


class TestKnobSemantics:
    """旋钮 → read/idle 相的三态（None=引擎默认 / 正数=耐心 / 0=显式关闭）。"""

    @pytest.mark.parametrize(
        ("knob", "expected"),
        [(None, DEFAULT_STREAM_TIMEOUT_S), (30.0, 30.0), (0.0, None)],
    )
    def test_idle_timeout_of(self, knob, expected):
        assert idle_timeout_of(knob) == expected

    def test_build_request_timeout_connect_floored(self):
        t = build_request_timeout(30.0)
        assert isinstance(t, httpx.Timeout)
        assert t.connect == CONNECT_TIMEOUT_S
        assert t.pool == CONNECT_TIMEOUT_S
        assert t.read == 30.0
        assert t.write == 30.0

    def test_build_request_timeout_none_uses_engine_default(self):
        t = build_request_timeout(None)
        assert t.read == DEFAULT_STREAM_TIMEOUT_S

    def test_build_request_timeout_zero_disables_read_only(self):
        t = build_request_timeout(0.0)
        assert t.read is None
        assert t.connect == CONNECT_TIMEOUT_S  # connect 上限策略层关不掉

    def test_build_request_timeout_httpx2_variant(self):
        t = build_request_timeout_httpx2(30.0)
        assert isinstance(t, httpx2.Timeout)
        assert t.connect == CONNECT_TIMEOUT_S
        assert t.read == 30.0


class _Scripted:
    """按脚本出事件的流：``events`` 为 (delay_s, value) 序列。"""

    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        async def _gen():
            for delay, value in self._events:
                await asyncio.sleep(delay)
                yield value

        return _gen()


@pytest.mark.asyncio
class TestIdleGuarded:
    async def test_events_within_budget_pass_through(self):
        stream = _Scripted([(0.01, "a"), (0.01, "b")])
        assert [e async for e in idle_guarded(stream, 1.0)] == ["a", "b"]

    async def test_stalled_stream_dies_with_request_timeout(self):
        """事件间长沉默：处死当前流，死因 REQUEST_TIMEOUT（可重试家族）。"""
        stream = _Scripted([(0.01, "a"), (10.0, "b")])
        seen = []
        with pytest.raises(RequestTimeoutError) as exc_info:
            async for e in idle_guarded(stream, 0.05):
                seen.append(e)
        assert seen == ["a"]  # 已交付的事件不丢
        assert exc_info.value.nova_error_kind is NovaErrorKind.REQUEST_TIMEOUT

    async def test_stream_end_is_not_timeout(self):
        """流正常结束（StopAsyncIteration）不被误判为超时。"""
        stream = _Scripted([(0.01, "a")])
        assert [e async for e in idle_guarded(stream, 1.0)] == ["a"]

    async def test_none_disables_watchdog(self):
        stream = _Scripted([(0.05, "a")])
        assert [e async for e in idle_guarded(stream, None)] == ["a"]
