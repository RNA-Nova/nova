"""await_with_signal 竞速原语测试（对位 concurrency.md §六 完成优先裁决）。

竞速/中断一律 Event 受控完成序（testing-modeling §七——禁 sleep 表达
等待完成；sleep(0) 仅作事件循环出让点，不作时序依据）。

`await_with_signal` 走 `nova_protocol.signal` 模块路径导入——hub 门面
（nova_protocol/__init__.py）的导出待词汇批次捎上。
"""

import asyncio

import pytest
from nova_protocol import AbortController, AbortedError, AbortSignal
from nova_protocol.signal import await_with_signal


class TestAwaitWithSignal:
    """竞速原语 await_with_signal（对位 concurrency.md §六 完成优先裁决）。

    竞速/中断一律 Event 受控完成序（testing-modeling §七——禁 sleep 表达
    等待完成；sleep(0) 仅作事件循环出让点，不作时序依据）。
    """

    @pytest.mark.asyncio
    async def test_none_signal_passthrough(self):
        """signal=None 直通：返回结果，不建竞速任务"""

        async def op():
            return 42

        assert await await_with_signal(op(), None) == 42

    @pytest.mark.asyncio
    async def test_pre_aborted_fast_fail(self):
        """预触发信号：举旗点快失败——operation 从未启动（协程被显式关闭）"""
        controller = AbortController()
        controller.abort()
        started = False

        async def op():
            nonlocal started
            started = True

        with pytest.raises(AbortedError):
            await await_with_signal(op(), controller.signal)
        assert started is False

    @pytest.mark.asyncio
    async def test_operation_error_propagates(self):
        """operation 自身异常原样归属（signal 未触发时不吞不译）"""
        controller = AbortController()

        async def op():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await await_with_signal(op(), controller.signal)

    @pytest.mark.asyncio
    async def test_signal_wins_cancels_operation(self):
        """在飞竞速 signal 胜出：operation 亲见取消并落定（防悬挂任务泄漏），
        抛 AbortedError"""
        controller = AbortController()
        entered = asyncio.Event()
        seen_cancel = False

        async def op():
            nonlocal seen_cancel
            entered.set()
            try:
                await asyncio.Event().wait()  # 永不置位——只能被取消
            except asyncio.CancelledError:
                seen_cancel = True
                raise

        race = asyncio.create_task(await_with_signal(op(), controller.signal))
        await entered.wait()  # op 已启动进入等待（Event 受控）
        controller.abort()
        with pytest.raises(AbortedError):
            await race
        assert seen_cancel is True  # operation 被取消且取消落定后才抛出

    @pytest.mark.asyncio
    async def test_completion_wins_same_tick(self):
        """同刻就绪完成优先（completion-wins）：op 结果先行落地、abort 同刻
        触发——已完成的工作不丢弃（对位 concurrency.md §六）"""
        controller = AbortController()
        entered = asyncio.Event()
        gate = asyncio.Event()

        async def op():
            entered.set()
            await gate.wait()
            return "done"

        race = asyncio.create_task(await_with_signal(op(), controller.signal))
        await entered.wait()
        # 同 tick 布置：先放行 op（其 wakeup 先入就绪队列，竞速恢复时两者
        # 皆已完成）再 abort——裁决完成优先
        gate.set()
        controller.abort()
        assert await race == "done"
        assert controller.signal.aborted is True

    @pytest.mark.asyncio
    async def test_external_cancel_propagates_as_cancelled_error(self):
        """本任务被外部取消（基础设施收尾语义）：CancelledError 照常传播
        （不得翻译成 AbortedError——两词汇永不混用），operation 一并取消
        落定；signal 未触发"""
        controller = AbortController()
        entered = asyncio.Event()
        op_cancelled = False

        async def op():
            nonlocal op_cancelled
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                op_cancelled = True
                raise

        race = asyncio.create_task(await_with_signal(op(), controller.signal))
        await entered.wait()
        race.cancel()
        with pytest.raises(asyncio.CancelledError):
            await race
        assert op_cancelled is True
        assert controller.signal.aborted is False
