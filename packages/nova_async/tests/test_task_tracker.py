"""TaskTracker 行为测试（属主级任务台账）。"""

import asyncio
import logging

import pytest
from nova_async import TaskTracker
from nova_protocol import AbortedError


@pytest.mark.asyncio
async def test_spawn_tracks_and_auto_discards():
    tracker = TaskTracker(name="t")
    entered = asyncio.Event()

    async def job():
        entered.set()

    task = tracker.spawn(job())
    assert task is not None
    assert len(tracker) == 1

    await task
    await asyncio.sleep(0)  # 让 done callback 跑完
    assert len(tracker) == 0  # 完成自摘
    assert entered.is_set()


@pytest.mark.asyncio
async def test_spawn_after_close_returns_none_and_never_runs():
    tracker = TaskTracker()
    tracker.close()

    ran = False

    async def job():
        nonlocal ran
        ran = True

    task = tracker.spawn(job())
    assert task is None  # 晚到任务直接丢弃
    await asyncio.sleep(0.01)
    assert ran is False
    assert len(tracker) == 0


@pytest.mark.asyncio
async def test_cancel_all_cancels_inflight():
    tracker = TaskTracker()
    started = asyncio.Event()

    async def hanging():
        started.set()
        await asyncio.sleep(3600)

    tracker.spawn(hanging())
    tracker.spawn(hanging())
    await started.wait()

    tracker.close()
    tracker.cancel_all()  # 同步举旗
    await asyncio.wait_for(tracker.wait(), timeout=1.0)  # wait 排水收完
    assert len(tracker) == 0


@pytest.mark.asyncio
async def test_wait_drains_inflight():
    tracker = TaskTracker()
    done_count = 0

    async def quick():
        nonlocal done_count
        await asyncio.sleep(0.01)
        done_count += 1

    tracker.spawn(quick())
    tracker.spawn(quick())
    tracker.close()
    await tracker.wait()
    assert done_count == 2
    assert len(tracker) == 0


@pytest.mark.asyncio
async def test_failed_task_surfaces_via_log(caplog):
    """异常终结的任务强制浮出（兼作 never-retrieved 的合法消费）。"""
    tracker = TaskTracker(name="boom-ledger")

    async def failing():
        raise ValueError("kaboom")

    tracker.spawn(failing())
    with caplog.at_level(logging.ERROR):
        await asyncio.sleep(0.05)
    assert len(tracker) == 0
    assert any("boom-ledger" in rec.message for rec in caplog.records)
    assert any(rec.exc_info for rec in caplog.records)


@pytest.mark.asyncio
async def test_cancelled_task_does_not_log_error(caplog):
    tracker = TaskTracker(name="quiet")

    async def hanging():
        await asyncio.sleep(3600)

    tracker.spawn(hanging())
    await asyncio.sleep(0)
    with caplog.at_level(logging.ERROR):
        tracker.cancel_all()
        await tracker.wait()
    assert not caplog.records  # 取消不是失败，不报 error


@pytest.mark.asyncio
async def test_aborted_error_does_not_log_error(caplog):
    tracker = TaskTracker(name="abort-vocabulary")

    async def aborting():
        raise AbortedError("stop requested")

    tracker.spawn(aborting())
    with caplog.at_level(logging.ERROR):
        await asyncio.sleep(0.05)
    assert not caplog.records  # AbortedError 同样是取消词汇，不是故障


def test_closed_flag_and_repr():
    tracker = TaskTracker(name="x")
    assert tracker.closed is False
    tracker.close()
    tracker.close()  # 幂等
    assert tracker.closed is True
    assert "CLOSED" in repr(tracker)


@pytest.mark.asyncio
async def test_wait_returns_immediately_when_closed_and_empty():
    tracker = TaskTracker()
    tracker.close()
    await asyncio.wait_for(tracker.wait(), timeout=0.1)  # 不悬挂即通过


@pytest.mark.asyncio
async def test_len_reflects_inflight():
    tracker = TaskTracker()
    blocker = asyncio.Event()

    async def hanging():
        await blocker.wait()

    tracker.spawn(hanging())
    tracker.spawn(hanging())
    await asyncio.sleep(0)
    assert len(tracker) == 2
    blocker.set()
    await asyncio.sleep(0.05)
    assert len(tracker) == 0


@pytest.mark.asyncio
async def test_one_failure_does_not_affect_others(caplog):
    """单个任务异常终结：兄弟任务照常跑完，异常经日志浮出。"""
    tracker = TaskTracker(name="mixed")
    finished = asyncio.Event()

    async def failing():
        raise ValueError("one bad apple")

    async def fine():
        finished.set()

    tracker.spawn(failing())
    tracker.spawn(fine())
    with caplog.at_level(logging.ERROR):
        await asyncio.sleep(0.05)
    assert finished.is_set()
    assert len(tracker) == 0
    assert any("one bad apple" in str(rec.exc_info[1]) for rec in caplog.records)


@pytest.mark.asyncio
async def test_async_context_manager_teardown_on_normal_exit():
    """async CM 正常退出即三段式收摊：落闸 + 集内任务被取消 + 排水完毕。"""
    tracker = TaskTracker(name="cm")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async with tracker:
        task = tracker.spawn(hanging())
        assert task is not None
        await started.wait()
        assert len(tracker) == 1
    assert tracker.closed
    assert cancelled.is_set()
    assert task.cancelled()
    assert len(tracker) == 0


@pytest.mark.asyncio
async def test_async_context_manager_teardown_on_exception_exit():
    """CM 异常退出同样收摊，且不压制原异常（__aexit__ 返回 None）。"""
    tracker = TaskTracker(name="cm-boom")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(RuntimeError, match="body blew up"):
        async with tracker:
            tracker.spawn(hanging())
            await started.wait()
            raise RuntimeError("body blew up")
    assert tracker.closed
    assert cancelled.is_set()
    assert len(tracker) == 0


@pytest.mark.asyncio
async def test_cancel_all_and_wait_from_tracked_task_no_deadlock_no_suicide():
    """从台账内任务发起 cancel_all + wait：不死锁、不自杀（跳过当前任务），
    兄弟任务照常取消并排水。受控完成序全程 asyncio.Event，无 sleep 等待。"""
    tracker = TaskTracker(name="self-tear")
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    inner_done = asyncio.Event()

    async def sibling():
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    async def inner():
        # 等兄弟任务真起跑再收摊（否则举旗抢在兄弟首步前，started 永不到达）
        await sibling_started.wait()
        # 台账内任务发起收摊：不杀自己（cancel_all 跳过当前任务）、
        # 不等自己（wait 跳过当前任务——等自己 = 死锁）
        tracker.close()
        tracker.cancel_all()
        await tracker.wait()
        inner_done.set()

    inner_task = tracker.spawn(inner())
    tracker.spawn(sibling())
    await sibling_started.wait()
    await asyncio.wait_for(inner_task, timeout=1.0)  # 死锁则此处超时判红
    assert inner_done.is_set()
    assert sibling_cancelled.is_set()
    assert len(tracker) == 0
