"""
通用存储后端抽象。

提供文件锁 + 读写、内存两种 backend，供 settings 和 auth 等配置模块复用。
"""

import os
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

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
        lock = FileLock(str(self._lock_path), timeout=self._timeout)
        try:
            with lock:
                yield
        except Timeout:
            raise RuntimeError(
                f"Could not acquire lock for {self._path} within {self._timeout} seconds. "
                f"Another process may be holding the lock. "
                f"Lock file: {self._lock_path}"
            ) from None

    def with_lock(self, fn: Callable[[Optional[str]], Optional[str]]) -> None:
        with self._acquire_lock():
            current = (
                self._path.read_text(encoding="utf-8") if self._path.exists() else None
            )
            next_content = fn(current)
            if next_content is not None:
                self._ensure_parent()
                self._path.write_text(next_content, encoding="utf-8")
                if self._file_mode is not None:
                    os.chmod(self._path, self._file_mode)


class InMemoryStorageBackend(StorageBackend):
    """内存存储后端，主要用于测试。"""

    def __init__(self, initial: Optional[str] = None) -> None:
        self._value = initial

    def with_lock(self, fn: Callable[[Optional[str]], Optional[str]]) -> None:
        next_value = fn(self._value)
        if next_value is not None:
            self._value = next_value
