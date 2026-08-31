"""
通用存储后端抽象。

提供文件锁 + 读写、内存两种 backend，供 settings 和 auth 等配置模块复用。

- ``with_lock(fn)``：同步临界区，``fn(current_content) -> next_content | None``
- ``with_lock_async(fn)``：异步临界区（锁可跨越 await 持有），
  ``fn(current_content) -> (result, next_content | None)``，
  用于需要在锁内执行异步操作的场景（如 OAuth token 刷新）
"""

import asyncio
import os
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Tuple

from filelock import FileLock, Timeout


class StorageBackend(ABC):
    """抽象存储后端。

    调用方通过 ``with_lock(fn)`` 在锁保护下执行读写：
    ``fn(current_content) -> next_content | None``。
    返回值非 None 时写回存储。
    """

    @abstractmethod
    def with_lock(self, fn: Callable[[Optional[str]], Optional[str]]) -> None:
        """在锁保护下执行 fn，必要时写回新内容。"""
        raise NotImplementedError

    @abstractmethod
    async def with_lock_async(
        self, fn: Callable[[Optional[str]], Awaitable[Tuple[Any, Optional[str]]]]
    ) -> Any:
        """在锁保护下执行异步 fn，返回 (result, next_content) 中的 result。"""
        raise NotImplementedError


class FileStorageBackend(StorageBackend):
    """基于文件锁的存储后端。"""

    def __init__(
        self,
        path: Path,
        timeout: float = 30.0,
        file_mode: Optional[int] = None,
        dir_mode: Optional[int] = None,
        initial_content: str = "",
    ) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._timeout = timeout
        self._file_mode = file_mode
        self._dir_mode = dir_mode
        self._initial_content = initial_content

    def _ensure_parent(self) -> None:
        parent = self._path.parent
        if not parent.exists():
            kwargs: dict[str, Any] = {"parents": True, "exist_ok": True}
            if self._dir_mode is not None:
                kwargs["mode"] = self._dir_mode
            parent.mkdir(**kwargs)

    def _ensure_file(self) -> None:
        if not self._path.exists():
            self._ensure_parent()
            self._path.write_text(self._initial_content, encoding="utf-8")
            if self._file_mode is not None:
                os.chmod(self._path, self._file_mode)

    @contextmanager
    def _acquire_lock(self):
        self._ensure_parent()
        self._ensure_file()
        lock = FileLock(str(self._lock_path))
        # 使用短重试而不是一次性长超时，提高并发获取锁的响应性。
        retries = 10
        delay_ms = 20
        for attempt in range(retries):
            try:
                with lock.acquire(timeout=self._timeout / retries):
                    yield
                    return
            except Timeout:
                if attempt == retries - 1:
                    raise RuntimeError(
                        f"Could not acquire lock for {self._path} within "
                        f"{self._timeout} seconds. Another process may be holding "
                        f"the lock. Lock file: {self._lock_path}"
                    ) from None
                time.sleep(delay_ms / 1000.0)

    def _read_content(self) -> Optional[str]:
        return self._path.read_text(encoding="utf-8") if self._path.exists() else None

    def _write_content(self, content: str) -> None:
        self._ensure_parent()
        self._path.write_text(content, encoding="utf-8")
        if self._file_mode is not None:
            os.chmod(self._path, self._file_mode)

    def with_lock(self, fn: Callable[[Optional[str]], Optional[str]]) -> None:
        with self._acquire_lock():
            next_content = fn(self._read_content())
            if next_content is not None:
                self._write_content(next_content)

    async def with_lock_async(
        self, fn: Callable[[Optional[str]], Awaitable[Tuple[Any, Optional[str]]]]
    ) -> Any:
        # 文件锁是进程级的同步锁，可以跨 await 持有；
        # 获取锁的重试用 asyncio.sleep 让出事件循环，避免与其他协程互相饿死。
        self._ensure_parent()
        self._ensure_file()
        lock = FileLock(str(self._lock_path))
        retries = 10
        acquired = False
        for attempt in range(retries):
            try:
                # 短超时阻塞尝试，失败则异步让出后重试
                lock.acquire(timeout=min(self._timeout / retries, 0.05))
                acquired = True
                break
            except Timeout:
                if attempt == retries - 1:
                    raise RuntimeError(
                        f"Could not acquire lock for {self._path} within "
                        f"{self._timeout} seconds. Another process may be holding "
                        f"the lock. Lock file: {self._lock_path}"
                    ) from None
                await asyncio.sleep(0.02)
        try:
            result, next_content = await fn(self._read_content())
            if next_content is not None:
                self._write_content(next_content)
            return result
        finally:
            if acquired:
                try:
                    lock.release()
                except Exception:
                    pass


class InMemoryStorageBackend(StorageBackend):
    """内存存储后端，主要用于测试。"""

    def __init__(self, initial: Optional[str] = None) -> None:
        self._value = initial

    def with_lock(self, fn: Callable[[Optional[str]], Optional[str]]) -> None:
        next_value = fn(self._value)
        if next_value is not None:
            self._value = next_value

    async def with_lock_async(
        self, fn: Callable[[Optional[str]], Awaitable[Tuple[Any, Optional[str]]]]
    ) -> Any:
        result, next_value = await fn(self._value)
        if next_value is not None:
            self._value = next_value
        return result
