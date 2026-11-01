"""可中断等待原语测试（``nova_ai/utils/abort.py``）。

竞速编排一律同步原语（Event 受控完成序），sleep 仅作 ≤0.05s 引导——
见 ``docs/crosscutting/testing-modeling.md`` §七。
"""

import asyncio

import pytest
from nova_protocol import AbortController, AbortedError

from nova_ai.utils.abort import AbortScope, await_with_abort

pytestmark = pytest.mark.asyncio


async def test_abort_scope_kills_hanging_wait_on_signal():
    """signal 触发：块内挂起的等待被**真杀**（op 亲见 CancelledError），
    块后归因翻译抛 AbortedError。"""
    controller = AbortController()
    started = asyncio.Event()
    killed = asyncio.Event()
    never = asyncio.Event()

    async def op():
        started.set()
        try:
            await never.wait()
        except asyncio.CancelledError:
            killed.set()
            raise

    abort_scope = AbortScope(controller.signal)

    async def driver():
        with abort_scope:
            await op()
        abort_scope.raise_if_aborted()

    task = asyncio.ensure_future(driver())
    await started.wait()
    controller.abort()
    with pytest.raises(AbortedError):
        await asyncio.wait_for(task, timeout=2)
    assert killed.is_set()  # 真杀——不是"不等了"，是代码死了


async def test_abort_scope_no_signal_completes():
    abort_scope = AbortScope(None)
    with abort_scope:
        pass
    abort_scope.raise_if_aborted()  # 不抛


async def test_abort_scope_completion_without_fire_no_raise():
    controller = AbortController()
    abort_scope = AbortScope(controller.signal)
    with abort_scope:
        pass
    abort_scope.raise_if_aborted()  # 不抛


async def test_abort_scope_signal_after_completion_still_aborts():
    """块正常完成后 signal 才触发：同样判取消（举旗即取消语义）。"""
    controller = AbortController()
    abort_scope = AbortScope(controller.signal)
    with abort_scope:
        pass
    controller.abort()
    with pytest.raises(AbortedError):
        abort_scope.raise_if_aborted()


async def test_abort_scope_cleanup_outside_scope_runs():
    """scope 取消后，块外的清理 await 正常执行（cleanup 必须在块外——
    level-triggered 语义下块内 await 会持续抛取消）。"""
    controller = AbortController()
    started = asyncio.Event()
    never = asyncio.Event()
    cleaned = asyncio.Event()

    async def op():
        started.set()
        await never.wait()

    abort_scope = AbortScope(controller.signal)

    async def driver():
        with abort_scope:
            await op()
        # 块外清理（关流/关 client 的位置）
        await asyncio.sleep(0)
        cleaned.set()
        abort_scope.raise_if_aborted()

    task = asyncio.ensure_future(driver())
    await started.wait()
    controller.abort()
    with pytest.raises(AbortedError):
        await asyncio.wait_for(task, timeout=2)
    assert cleaned.is_set()


async def test_abort_scope_pre_aborted_signal():
    """预取消信号：块内首个 checkpoint（await 点）即死，后续代码不执行。"""
    controller = AbortController()
    controller.abort()
    proceeded = False

    abort_scope = AbortScope(controller.signal)
    with abort_scope:
        await asyncio.sleep(0)  # 首个 checkpoint
        proceeded = True
    assert proceeded is False
    with pytest.raises(AbortedError):
        abort_scope.raise_if_aborted()


async def test_await_with_abort_returns_result():
    controller = AbortController()

    async def op():
        return "ok"

    assert await await_with_abort(op(), controller.signal) == "ok"


async def test_await_with_abort_kills_hanging_operation():
    """一次性糖：signal 触发即真杀（op 亲见 CancelledError）+ AbortedError。"""
    controller = AbortController()
    started = asyncio.Event()
    killed = asyncio.Event()
    never = asyncio.Event()

    async def op():
        started.set()
        try:
            await never.wait()
        except asyncio.CancelledError:
            killed.set()
            raise

    task = asyncio.ensure_future(await_with_abort(op(), controller.signal))
    await started.wait()
    controller.abort()
    with pytest.raises(AbortedError):
        await asyncio.wait_for(task, timeout=2)
    assert killed.is_set()


async def test_abort_scope_detaches_listener_on_exception_path():
    """回归：异常穿过 scope 块时（raise_if_aborted 永不到达），signal 的
    监听器也必须解绑——否则长寿命 signal 的监听器列表随失败请求泄漏累积。"""
    controller = AbortController()
    signal = controller.signal

    abort_scope = AbortScope(signal)
    with pytest.raises(ValueError):
        with abort_scope:
            raise ValueError("boom")
    # 异常路径后：监听器已解绑（abort 时不再有回调触发已死 scope）
    assert not signal._callbacks  # 无残留监听
