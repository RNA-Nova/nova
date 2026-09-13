"""TaskTracker 行为测试（属主级任务台账）。"""

import asyncio
import logging

import pytest

from nova_ai.utils.task_tracker import TaskTracker


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
    await asyncio.wait_for(tracker.cancel_all(), timeout=1.0)
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
        await tracker.cancel_all()
    assert not caplog.records  # 取消不是失败，不报 error


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
