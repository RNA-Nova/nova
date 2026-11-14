"""AbortSignal / AbortController（signal.py）测试——对齐 TS AbortController 语义。

纪律：只经公开面（`AbortController.abort()`）驱动——`signal._trigger` 是
内部实现，不测私有函数（testing-modeling §九）。
"""

import asyncio

import pytest
from nova_async import AbortController, AbortedError, AbortSignal


class TestAbortSignal:
    def test_initial_state_not_aborted(self):
        signal = AbortSignal()
        assert signal.aborted is False

    def test_add_and_remove_listener(self):
        controller = AbortController()
        signal = controller.signal
        calls = []

        def _cb(_signal):
            calls.append(1)

        signal.add_event_listener(_cb)
        controller.abort()
        assert calls == [1]

        # 移除后不再触发（abort 幂等，监听器已摘）
        signal.remove_event_listener(_cb)
        controller.abort()
        assert calls == [1]

    def test_remove_missing_listener_no_error(self):
        controller = AbortController()
        calls = []
        controller.signal.add_event_listener(lambda _s: calls.append(1))
        # 移除不存在的监听器不抛异常，且不影响已注册的监听器
        controller.signal.remove_event_listener(lambda _s: None)
        controller.abort()
        assert calls == [1]

    def test_listener_exception_does_not_break_others(self):
        controller = AbortController()
        calls = []

        def _bad(_signal):
            raise RuntimeError("boom")

        def _good(_signal):
            calls.append(1)

        controller.signal.add_event_listener(_bad)
        controller.signal.add_event_listener(_good)
        controller.abort()
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
        """signal 不暴露触发接口，唯一触发方式是 controller.abort()。"""
        controller = AbortController()
        assert not hasattr(controller.signal, "abort")
        assert not hasattr(controller.signal, "_trigger_public")


def test_any_does_not_leak_listeners_on_sources():
    """any() 组合信号被丢弃后源上不累积监听器（组合信号强持控制器保活、
    源侧闭包弱持——丢弃即全链可 GC，源下次 abort 时自摘）。

    此前源侧闭包强持控制器：长寿命源上每轮 any() 永久累积一个闭包。
    """
    import gc
    import weakref

    from nova_async import AbortSignal

    source_a, source_b = AbortController(), AbortController()
    combined_refs = []
    for _ in range(3):
        combined = AbortSignal.any([source_a.signal, source_b.signal])
        combined_refs.append(weakref.ref(combined))
        del combined
    gc.collect()
    # 核心主张：组合信号被丢弃即全链可回收
    assert all(ref() is None for ref in combined_refs)
    # 源下次 abort 时死组合信号的闭包自摘
    source_a.abort()
    assert len(source_a.signal._callbacks) == 0
    assert len(source_b.signal._callbacks) == 0


def test_any_live_path_still_fires():
    """any() 活路径不回归：组合信号活着时任一源中断即中断。"""

    from nova_async import AbortSignal

    source_a, source_b = AbortController(), AbortController()
    combined = AbortSignal.any([source_a.signal, source_b.signal])
    assert combined is not None
    source_a.abort()
    assert combined.aborted is True
