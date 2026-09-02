"""file_queue 写锁测试：realpath 锁键归一（软链共享锁）与锁回收。"""

import asyncio
import os

from nova_coding_agent.tools_common import file_queue
from nova_coding_agent.tools_common.file_queue import (
    FileWriteQueue,
    with_file_write_lock,
)


def test_lock_key_realpath_normalizes_symlink(tmp_path):
    """软链/别名路径与目标路径归一到同一锁键（共享同一把锁）。"""
    real = tmp_path / "real.txt"
    real.write_text("x")
    alias = tmp_path / "alias.txt"
    os.symlink(real, alias)

    assert file_queue._lock_key(str(alias)) == file_queue._lock_key(str(real))


def test_lock_key_fallback_normpath_on_realpath_failure(monkeypatch):
    """realpath 失败（如 ENOENT）时回退 normpath 作为锁键。"""

    def _raise(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(os.path, "realpath", _raise)
    assert file_queue._lock_key("a/./b") == os.path.normpath("a/./b")


def test_lock_recycled_after_use(tmp_path):
    """锁用完从 _locks/_refcounts 删除（防长会话内存累积）。"""
    target = tmp_path / "f.txt"
    target.write_text("x")
    key = file_queue._lock_key(str(target))

    async def scenario():
        async with with_file_write_lock(str(target)):
            assert key in FileWriteQueue._locks
        assert key not in FileWriteQueue._locks
        assert key not in FileWriteQueue._refcounts

    asyncio.run(scenario())


def test_lock_not_recycled_while_waiter_pending(tmp_path):
    """有等待者时锁不提前回收（引用计数）；全部用完后才回收。"""
    target = tmp_path / "f.txt"
    target.write_text("x")
    key = file_queue._lock_key(str(target))

    async def _hold(delay):
        async with with_file_write_lock(str(target)):
            await asyncio.sleep(delay)

    async def scenario():
        first = asyncio.ensure_future(_hold(0.03))
        await asyncio.sleep(0)  # 让 first 先拿到锁
        async with with_file_write_lock(str(target)):
            # first 释放后锁被本协程接手，等待期间键必须一直在
            assert key in FileWriteQueue._locks
        await first
        assert key not in FileWriteQueue._locks
        assert key not in FileWriteQueue._refcounts

    asyncio.run(scenario())


def test_concurrent_writers_serialize_across_symlink_alias(tmp_path):
    """同一文件的软链别名共享同一把锁：并发写入串行执行、无交错。"""
    real = tmp_path / "real.txt"
    real.write_text("")
    alias = tmp_path / "alias.txt"
    os.symlink(real, alias)

    order = []

    async def worker(path, tag):
        async with with_file_write_lock(str(path)):
            order.append(f"start-{tag}")
            await asyncio.sleep(0.02)
            order.append(f"end-{tag}")

    async def scenario():
        await asyncio.gather(worker(str(real), "a"), worker(str(alias), "b"))

    asyncio.run(scenario())
    assert order in (
        ["start-a", "end-a", "start-b", "end-b"],
        ["start-b", "end-b", "start-a", "end-a"],
    )
