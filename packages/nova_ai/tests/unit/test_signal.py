"""AbortSignal / AbortController 测试（对齐 TS AbortController 语义）。"""

import asyncio

import pytest

from nova_ai import AbortController, AbortSignal


class TestAbortSignal:
    def test_initial_state_not_aborted(self):
        signal = AbortSignal()
        assert signal.aborted is False

    def test_add_and_remove_listener(self):
        signal = AbortSignal()
        calls = []

        def _cb(_signal):
            calls.append(1)

        signal.add_event_listener(_cb)
        signal._trigger()
        assert calls == [1]

        # 移除后不再触发（signal 已 aborted，_trigger 幂等）
        signal.remove_event_listener(_cb)
        signal._trigger()
        assert calls == [1]

    def test_remove_missing_listener_no_error(self):
        signal = AbortSignal()
        calls = []
        signal.add_event_listener(lambda _s: calls.append(1))
        # 移除不存在的监听器不抛异常，且不影响已注册的监听器
        signal.remove_event_listener(lambda _s: None)
        signal._trigger()
        assert calls == [1]

    def test_listener_exception_does_not_break_others(self):
        signal = AbortSignal()
        calls = []

        def _bad(_signal):
            raise RuntimeError("boom")

        def _good(_signal):
            calls.append(1)

        signal.add_event_listener(_bad)
        signal.add_event_listener(_good)
        signal._trigger()
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_wait_returns_immediately_when_aborted(self):
        controller = AbortController()
        controller.abort()
        await asyncio.wait_for(controller.signal.wait(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_wait_resolves_on_later_abort(self):
        controller = AbortController()

        async def _abort_soon():
            await asyncio.sleep(0.01)
            controller.abort()

        asyncio.create_task(_abort_soon())
        await asyncio.wait_for(controller.signal.wait(), timeout=1.0)
        assert controller.signal.aborted is True


class TestAbortController:
    def test_abort_sets_signal_aborted(self):
        controller = AbortController()
        assert controller.signal.aborted is False
        controller.abort()
        assert controller.signal.aborted is True

    def test_abort_is_idempotent(self):
        controller = AbortController()
        calls = []
        controller.signal.add_event_listener(lambda _s: calls.append(1))
        controller.abort()
        controller.abort()
        assert calls == [1]

    def test_signal_is_readonly_view(self):
        """signal 不暴露触发接口，唯一触发方式是 controller.abort()"""
        controller = AbortController()
        assert not hasattr(controller.signal, "abort")
        assert not hasattr(controller.signal, "_trigger_public")
