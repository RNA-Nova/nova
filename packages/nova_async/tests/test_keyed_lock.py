"""keyed_lock per-key 编排锁语义测试（同步族）。

只锁 per-key 编排锁的语义：同 key 串行去重、异 key 不互阻、失败不毒化、
工厂 memoize。
"""

import asyncio

import pytest
from nova_async import in_memory_keyed_locks

pytestmark = pytest.mark.asyncio


async def test_keyed_lock_serializes_per_key():
    """同 key 的临界区串行（编排去重的地基）。"""
    locks = in_memory_keyed_locks()
    order = []

    async def _critical(name: str, delay: float) -> None:
        async with locks("k1"):
            order.append(f"{name}-start")
            await asyncio.sleep(delay)
            order.append(f"{name}-end")

    await asyncio.gather(_critical("A", 0.05), _critical("B", 0.01))
    assert order == ["A-start", "A-end", "B-start", "B-end"]


async def test_keyed_lock_does_not_block_other_keys():
    """不同 key 互不阻塞（锁粒度存在的理由）。"""
    locks = in_memory_keyed_locks()
    order = []

    async def _critical(key: str, name: str, delay: float) -> None:
        async with locks(key):
            order.append(f"{name}-start")
            await asyncio.sleep(delay)
            order.append(f"{name}-end")

    await asyncio.gather(_critical("k1", "A", 0.05), _critical("k2", "B", 0.01))
    # k2 不被 k1 挡：B 应先完成
    assert order.index("B-end") < order.index("A-end")


async def test_keyed_lock_failure_does_not_poison():
    """临界区内抛错正常释放，后续可进入（失败不毒化锁）。"""
    locks = in_memory_keyed_locks()

    with pytest.raises(RuntimeError):
        async with locks("k1"):
            raise RuntimeError("boom")

    entered = False
    async with locks("k1"):
        entered = True
    assert entered is True


async def test_keyed_lock_factory_memoizes_per_key():
    """同一 key 返回同一把锁（跨调用共享=去重才成立）。"""
    locks = in_memory_keyed_locks()
    assert locks("k1") is locks("k1")
    assert locks("k1") is not locks("k2")


async def test_default_keyed_locks_warns_once(caplog, monkeypatch):
    """缺省兜底锁首次使用打一条每进程一次的 warning（多进程部署提示）。"""
    import logging

    import nova_async.keyed_lock as kl

    monkeypatch.setattr(kl, "_default_locks_warned", False)
    with caplog.at_level(logging.WARNING, logger="nova_async.keyed_lock"):
        kl.default_keyed_locks("k1")
        kl.default_keyed_locks("k2")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "进程内" in warnings[0].message
