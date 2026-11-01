"""KeyedLock —— per-key 编排锁（同步族·per-key 编排锁）。

族与纪律（``docs/crosscutting/structure-modeling.md`` §二第 2 层族文件）：

- **同 key 串行、异 key 并行**：防同类并发编排撞车（如轮转 token 双刷、
  刷新与登出串号）；锁只住编排层——底层 store 已同步化的不需要也不应该
  知道它；
- **注入式（port）**：默认进程内 ``asyncio.Lock``（单进程与测试路径）；
  跨进程部署由上层注入文件锁实现（adapter，住 nova_harness 不动）；
- **可取消性归外层**：锁等待的取消由外层 ``AbortScope`` 提供（锁获取的
  等待点会被 scope 真杀；文件锁实现的异步让出重试循环见 harness 侧实现）。

升格自 nova_ai ``auth/refresh_locks.py``（harness 文件锁实现跨包 import
协议触发 §二升格出口）——命名泛化为 per-key，调用点域语义（kwarg/属性名
``refresh_locks``）不动。
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncContextManager, Callable, Dict

logger = logging.getLogger(__name__)

KeyedLockFactory = Callable[[str], AsyncContextManager[None]]
"""per-key 异步锁工厂：入参 key，返回异步上下文管理器。"""


def in_memory_keyed_locks() -> KeyedLockFactory:
    """进程内 per-key 锁（memoized ``asyncio.Lock``——codex manager 信号量形状）。"""
    locks: Dict[str, asyncio.Lock] = {}

    def _factory(key: str) -> AsyncContextManager[None]:
        return locks.setdefault(key, asyncio.Lock())

    return _factory


# 缺省锁工厂（进程内去重）——未显式注入时的兜底
_default_locks = in_memory_keyed_locks()
_default_locks_warned = False


def default_keyed_locks(key: str) -> AsyncContextManager[None]:
    """缺省锁工厂（进程内去重）——未显式注入时的兜底。

    首次调用打一条每进程一次的 warning：单进程部署语义正确；多进程/多
    实例共享凭据文件时需注入文件锁 adapter（harness 侧
    ``file_refresh_locks``），否则跨进程去重缺位（轮转 token 可能双刷）。
    """
    global _default_locks_warned
    if not _default_locks_warned:
        _default_locks_warned = True
        logger.warning(
            "refresh 编排锁使用进程内实现（未注入跨进程锁）——单进程部署正确；"
            "多进程部署请注入 file_refresh_locks，否则轮转 token 可能双刷"
        )
    return _default_locks(key)


__all__ = ["KeyedLockFactory", "default_keyed_locks", "in_memory_keyed_locks"]
