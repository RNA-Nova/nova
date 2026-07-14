"""并发文件写入串行化队列。

多个 tool_call 同时写入同一文件时，通过单例 Lock 保证顺序执行，避免内容交错。
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict


class FileWriteQueue:
    """基于路径的异步写入队列。"""

    _locks: Dict[str, asyncio.Lock] = {}
    _global_lock = asyncio.Lock()

    @classmethod
    async def _get_lock(cls, path: str) -> asyncio.Lock:
        async with cls._global_lock:
            if path not in cls._locks:
                cls._locks[path] = asyncio.Lock()
            return cls._locks[path]


@asynccontextmanager
async def with_file_write_lock(path: str) -> AsyncGenerator[asyncio.Lock, None]:
    """获取指定路径的写入锁并作为异步上下文管理器返回。"""
    lock = await FileWriteQueue._get_lock(path)
    await lock.acquire()
    try:
        yield lock
    finally:
        lock.release()


__all__ = ["with_file_write_lock", "FileWriteQueue"]
