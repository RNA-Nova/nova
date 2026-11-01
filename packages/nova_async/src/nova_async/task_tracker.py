"""TaskTracker —— 属主级在途任务台账（对齐 codex/tokio-util 的 TaskTracker）。

归属：登记制基础原语（``docs/crosscutting/structure-modeling.md`` §二第
1 层——与 signal.py 同族的异步生命周期原语；全仓签名通用、零业务语义、
仅 stdlib）。此前住 nova_ai/utils 时被 nova_harness / nova_server 经模块
路径跨包 import（6 处），按升格入口迁入枢纽。

回答"这个属主名下现在活着哪些任务、收摊时它们怎么办"。一属主一实例
（会话/连接/runner 各自持有），账簿与属主同生共死；子部件需要往属主
账上登记时经参数拿到引用（Python 传引用即共享账簿，对应 Rust 侧的
clone 分发）。

纪律：

- **没有 reopen**：close 是单向门，关停即终态——"close 后不再有新任务"
  的推理无条件成立，级联收尾的正确性建立在它上面。账簿不跨代：
  新一代工作 = 新属主实例 = 自带新账；
- **收尾三段式**：属主先 ``close()`` 落闸（收尾竞态期的晚到任务直接
  丢弃），再 ``cancel_all()`` 同步举旗（硬取消——abort 信号之后的
  硬地板，可在同步关停路径调用），需要任务真正收完时
  ``await wait()`` 排水；
- **失败浮出**：任务异常终结时经 logger 强制上报（兼作 "Task exception
  was never retrieved" 的合法消费）——从结构上消灭静默吞异常。

与 ``nova_ai.streaming.EventStream.drive()`` 的分工：drive 管 1:1
（流即属主，流自持驱动任务），本类管 1:N（属主名下多任务台账）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger(__name__)


class TaskTracker:
    """属主级在途任务台账（用法与纪律见模块 docstring）。"""

    def __init__(self, *, name: str = "") -> None:
        self._name = name
        self._tasks: Set[asyncio.Task[Any]] = set()
        self._closed = False

    def __repr__(self) -> str:
        status = "CLOSED" if self._closed else "OPEN"
        return f"<TaskTracker {self._name or '(unnamed)'}: {status}, {len(self)} in-flight>"

    def __len__(self) -> int:
        """在途任务数（背压/过载计数等真实消费方用）。"""
        return len(self._tasks)

    @property
    def closed(self) -> bool:
        """是否已落闸。"""
        return self._closed

    def spawn(
        self, coro: Coroutine[Any, Any, Any], *, name: str = ""
    ) -> Optional[asyncio.Task[Any]]:
        """登记并启动任务（完成自摘，异常终结强制浮出）。

        close 后返回 ``None`` 且协程不起跑——调用方应把 ``None`` 当
        "属主已关停"处理（晚到任务直接丢弃是收尾竞态的正确语义）。
        """
        if self._closed:
            coro.close()  # 不起跑即关：防 "coroutine was never awaited" 警告
            return None
        task = asyncio.create_task(coro, name=name or None)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def close(self) -> None:
        """落闸：不再收新任务（幂等，单向门）。"""
        self._closed = True

    def cancel_all(self) -> None:
        """硬取消全部在途任务（同步举旗，不等待收尾）。

        属主收尾的两段式（codex token.cancel + tracker.wait 对位）：
        先 ``cancel_all()`` 同步举旗（可在同步关停路径调用），
        需要等任务真正收完时再 ``await wait()``。
        """
        for task in list(self._tasks):
            task.cancel()

    async def wait(self) -> None:
        """排水：等当前在途任务全部跑完。

        仅在 close 后调用才有意义——未落闸时新任务可随时流入，
        本调用可能永不返回。

        纪律（codex 同款）：关停从外部发起——**禁止从本台账内的任务
        调用 wait()**（等自己 = 死锁）。
        """
        while self._tasks:
            await asyncio.wait(list(self._tasks))

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        # task.exception() 的读取兼作 "never retrieved" 警告的合法消费
        exc = task.exception()
        if exc is not None:
            logger.error(
                "TaskTracker[%s] 任务 %r 异常终结",
                self._name or "(unnamed)",
                task.get_name(),
                exc_info=(type(exc), exc, exc.__traceback__),
            )


__all__ = ["TaskTracker"]
