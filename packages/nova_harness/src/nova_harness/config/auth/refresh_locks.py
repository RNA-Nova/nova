"""per-provider 刷新锁的文件实现（跨进程去重——生产注入）。

nova_ai 的编排层（resolve/ModelsManager）经 ``KeyedLockFactory`` 注入
（协议住 nova_async——per-key 编排锁 port）；本模块是 harness 侧的生产
实现：``auth-locks/<provider>.lock`` 一把锁文件管一个 provider——同
provider 的 refresh/logout/login 跨进程串行（防轮转 token 双刷），
不同 provider 互不阻塞。

锁等待的可取消性由外层 ``AbortScope`` 提供（重试循环的 ``asyncio.sleep``
会被 scope 真杀；filelock 为 OS 级锁，持锁进程死亡即自动释放）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncContextManager

from filelock import FileLock, Timeout
from nova_async import Deadline, KeyedLockFactory

_ACQUIRE_ATTEMPT_TIMEOUT_S = 0.05
_ACQUIRE_RETRY_INTERVAL_S = 0.05


class _FileRefreshLock:
    """单个 provider 的文件锁（异步上下文管理器）。"""

    def __init__(self, path: Path, timeout_s: float) -> None:
        self._lock = FileLock(str(path))
        self._timeout_s = timeout_s
        self._path = path

    async def __aenter__(self) -> None:
        # 获取总预算（nova_async Deadline 值对象——替代旧 loop.time() 手写
        # 算术：单调钟同源，expired 即 budget 耗尽）
        deadline = Deadline(timeout_ms=self._timeout_s * 1000)
        while True:
            try:
                # 短阻塞尝试 + 异步让出：不把事件循环冻在锁获取上
                self._lock.acquire(timeout=_ACQUIRE_ATTEMPT_TIMEOUT_S)
                return
            except Timeout:
                if deadline.expired:
                    raise RuntimeError(
                        f"Could not acquire refresh lock for {self._path.name} "
                        f"within {self._timeout_s}s. Another process may be "
                        "holding it during a long refresh."
                    ) from None
                await asyncio.sleep(_ACQUIRE_RETRY_INTERVAL_S)

    async def __aexit__(self, *_exc: object) -> None:
        try:
            self._lock.release()
        except Exception:
            pass


def file_refresh_locks(base_dir: Path, timeout_s: float = 30.0) -> KeyedLockFactory:
    """per-provider 文件锁工厂：``<base_dir>/<provider>.lock``。"""
    # 锁目录在工厂创建时落地（filelock 的 acquire 要求父目录存在）
    base_dir.mkdir(parents=True, exist_ok=True)

    def _factory(provider_id: str) -> AsyncContextManager[None]:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in provider_id)
        return _FileRefreshLock(Path(base_dir) / f"{safe}.lock", timeout_s)

    return _factory


__all__ = ["file_refresh_locks"]
