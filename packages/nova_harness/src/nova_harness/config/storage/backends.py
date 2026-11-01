"""
通用存储后端抽象（**同步引擎件**）。

提供文件锁 + 读写、内存两种 backend，供 settings 和 auth 等配置模块复用。

- ``with_lock(fn)``：同步临界区，``fn(current_content) -> next_content | None``；
  锁只护微秒级字节段，**永不跨网络/长操作**（编排归 manager 层，
  见 ``docs/crosscutting/concurrency.md``）；
- 写盘统一 temp+rename 原子写（读者要么看到旧文件要么看到新文件，
  撕读与崩溃半写在结构上不存在）。
"""

import os
import time
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

    def revision(self) -> Optional[str]:
        """存储内容的版本戳（供调用方做"变更才重载"比对——pi
        ``getFileRevision`` 对位）。``None`` = 无版本概念（内存后端）。"""
        return None


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

    def revision(self) -> Optional[str]:
        """文件后端的版本戳：mtime_ns + size。"""
        if not self._path.exists():
            return None
        stat = self._path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"

    def _write_content(self, content: str) -> None:
        # temp+rename 原子写：同目录临时文件 → os.replace（POSIX 原子；
        # 读者要么看到旧文件要么看到新文件，崩溃不留半个 JSON）
        self._ensure_parent()
        import tempfile

        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=self._path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                tmp.write(content)
            if self._file_mode is not None:
                os.chmod(tmp_path, self._file_mode)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def with_lock(self, fn: Callable[[Optional[str]], Optional[str]]) -> None:
        with self._acquire_lock():
            next_content = fn(self._read_content())
            if next_content is not None:
                self._write_content(next_content)


class InMemoryStorageBackend(StorageBackend):
    """内存存储后端，主要用于测试。"""

    def __init__(self, initial: Optional[str] = None) -> None:
        self._value = initial

    def with_lock(self, fn: Callable[[Optional[str]], Optional[str]]) -> None:
        next_value = fn(self._value)
        if next_value is not None:
            self._value = next_value
