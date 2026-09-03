"""并发文件写入串行化队列。

多个 tool_call 同时写入同一文件时，通过单例 Lock 保证顺序执行，避免内容交错。
锁键经 realpath 归一：软链/别名路径指向同一文件时共享同一把锁。
锁在最后一个使用者释放后从字典回收，避免长会话中 _locks 无限累积。
"""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict


def _lock_key(path: str) -> str:
    """锁键归一：realpath 解析软链/别名；realpath 失败（如 ENOENT）回退 normpath。"""
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.normpath(path)


class FileWriteQueue:
    """基于路径的异步写入队列。"""

    _locks: Dict[str, asyncio.Lock] = {}
    _refcounts: Dict[str, int] = {}  # 每个锁键的持锁/等锁协程数
    _global_lock = asyncio.Lock()

    @classmethod
    async def _acquire(cls, key: str) -> asyncio.Lock:
        """取锁并登记一个使用者（全局锁内完成，与回收互斥）。"""
        async with cls._global_lock:
            lock = cls._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                cls._locks[key] = lock
            cls._refcounts[key] = cls._refcounts.get(key, 0) + 1
            return lock

    @classmethod
    async def _release(cls, key: str) -> None:
        """注销一个使用者；归零时回收锁，防字典无限累积。"""
        async with cls._global_lock:
            remaining = cls._refcounts.get(key, 0) - 1
            if remaining > 0:
                cls._refcounts[key] = remaining
                return
            cls._refcounts.pop(key, None)
            cls._locks.pop(key, None)


@asynccontextmanager
async def with_file_write_lock(path: str) -> AsyncGenerator[asyncio.Lock, None]:
    """获取指定路径的写入锁并作为异步上下文管理器返回。"""
    key = _lock_key(path)
    lock = await FileWriteQueue._acquire(key)
    await lock.acquire()
    try:
        yield lock
    finally:
        lock.release()
        await FileWriteQueue._release(key)


__all__ = ["with_file_write_lock", "FileWriteQueue"]
