"""工具执行门：公平 FIFO 读写锁（asyncio）。

对齐 codex ``core/src/tools/parallel.rs`` 的 ``tokio::sync::RwLock`` 语义
（parallel 工具共享读、sequential 工具独占写），按 asyncio 单线程事件循环
移植——状态变迁只发生在持锁区或授予时刻，等待者取消（abort）即自摘。

公平性：FIFO 等待队列——写者排队后新读者不再插队（写者优先防饿死），
读者连续放行直到队首遇写者。
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Deque, Tuple


class ToolExecutionGate:
    """公平 FIFO 读写门。

    - ``acquire(write=False)``：读门（parallel 工具）——无写者持有且无等待
      队列时立即进入，否则 FIFO 排队；
    - ``acquire(write=True)``：写门（sequential 工具）——等全场读者排空且
      无持门写者后独占；
    - 等待中的取消（CancelledError 传播）会从等待队列自摘并撤销
      已授予的门（授予后尚未恢复运行的竞态窗口），保证被 abort 的
      调用永不起跑。
    """

    def __init__(self) -> None:
        self._readers = 0
        self._writer_active = False
        # FIFO 等待队列：(是否写者, 等待 future)
        self._queue: Deque[Tuple[bool, asyncio.Future[None]]] = deque()
        self._condition = asyncio.Condition()

    async def acquire(self, write: bool) -> None:
        """获取门（读=共享 / 写=独占）。取消安全。"""
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        async with self._condition:
            # 快速路径：读者且无写者活动且无排队
            if not write and not self._writer_active and not self._queue:
                self._readers += 1
                return
            self._queue.append((write, waiter))
            self._drain_locked()
        try:
            await waiter
        except BaseException:
            # 等待中被取消（abort）：自摘；若恰好已授予（结果已置、协程未恢复）
            # 则撤销授予——持门而不跑与起跑同罪
            async with self._condition:
                for index, (_, future) in enumerate(self._queue):
                    if future is waiter:
                        del self._queue[index]
                        break
                else:
                    if waiter.done() and not waiter.cancelled():
                        if write:
                            self._writer_active = False
                        else:
                            self._readers -= 1
                self._drain_locked()
            raise

    async def release(self, write: bool) -> None:
        """释放门（必须与 acquire 的 write 形态一致）。"""
        async with self._condition:
            if write:
                self._writer_active = False
            else:
                self._readers -= 1
            self._drain_locked()

    def _drain_locked(self) -> None:
        """按 FIFO 放行（须在持 ``_condition`` 锁时调用）。

        状态在授予时刻变迁（不是 waiter 恢复运行时）——防"写者已被授予
        但尚未恢复，读者经快速路径挤入"的竞态。
        """
        while self._queue:
            is_write, waiter = self._queue[0]
            if waiter.done():  # 已取消的等待者清出队列
                self._queue.popleft()
                continue
            if is_write:
                if self._readers == 0 and not self._writer_active:
                    self._queue.popleft()
                    self._writer_active = True
                    waiter.set_result(None)
                break
            if self._writer_active:
                break
            self._queue.popleft()
            self._readers += 1
            waiter.set_result(None)
