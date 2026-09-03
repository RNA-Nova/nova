"""文件系统层（FileSystemLayer）：六个 fs 工具 operations 的统一原语底。

设计：

- read/write/edit/ls/find/grep 六个工具的 operations 实现**参数化在本层
  之上**；
- 全 async：本地实现经 ``asyncio.to_thread`` 挪出事件循环（并行工具执行
  不冻结 loop，与 operations.py 的并发约定一致）；
- 语义按工具真实用法收敛：
  ``metadata`` 不存在时回 ``FsStat(exists=False)`` 而非抛错；
  ``list_dir`` 对不存在/非目录抛 ``FileNotFoundError``/``NotADirectoryError``
  （工具侧按异常类型给文案）。

实现：``LocalFileSystemLayer``（os/pathlib）。
"""

from __future__ import annotations

import asyncio
import errno
import os
import stat as stat_module
from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple


@dataclass(frozen=True)
class FsStat:
    """路径元信息（``exists=False`` 时其余字段无意义）。"""

    exists: bool
    is_file: bool = False
    is_dir: bool = False
    size: int = 0
    mtime_ms: int = 0


@dataclass(frozen=True)
class FsEntry:
    """目录条目（ls 消费）。"""

    name: str
    is_dir: bool


@dataclass(frozen=True)
class WalkItem:
    """遍历条目（find/grep 消费——路径为搜索根下的绝对路径）。"""

    path: str
    is_dir: bool


@dataclass(frozen=True)
class WalkResult:
    """遍历结果（truncated=True 表示触及条目上限被剪）。"""

    entries: Tuple[WalkItem, ...]
    truncated: bool = False


class FileSystemLayer(Protocol):
    """六个 fs 工具共享的文件系统原语（全 async）。"""

    async def read_bytes(self, path: str) -> bytes:
        """读取整个文件（大文件调用方自律——文本读取先分页/截断）。"""
        ...

    async def read_range(self, path: str, offset: int, length: int) -> bytes:
        """读取文件切片（图片魔数嗅探等只需头部字节的场景）。"""
        ...

    async def write_bytes(self, path: str, data: bytes) -> None:
        """写入文件（父目录准备是独立一步——见 create_dir）。"""
        ...

    async def metadata(self, path: str) -> FsStat:
        """路径元信息；不存在回 ``FsStat(exists=False)``（不抛错）。"""
        ...

    async def list_dir(self, path: str) -> List[FsEntry]:
        """列目录；不存在抛 ``FileNotFoundError``、非目录抛
        ``NotADirectoryError``（双实现同语义）。"""
        ...

    async def create_dir(self, path: str) -> None:
        """递归创建目录（已存在不报错）。"""
        ...

    async def walk(self, path: str, *, max_entries: int = 50_000) -> WalkResult:
        """递归遍历（含隐藏文件，与 fd/rg 的 --hidden 语义对齐）；
        不存在抛 ``FileNotFoundError``。"""
        ...

    async def check_writable(self, path: str) -> None:
        """写前 fail-fast 检查（edit 的 access 语义）：不存在抛
        ``FileNotFoundError``；本地附带 R_OK|W_OK 权限检查，远程写时
        自然报错（元信息无权限位，尽力而为）。"""
        ...


class LocalFileSystemLayer:
    """本地文件系统 FileSystemLayer 实现（阻塞调用一律 to_thread）。"""

    async def read_bytes(self, path: str) -> bytes:
        def _read() -> bytes:
            with open(path, "rb") as f:
                return f.read()

        return await asyncio.to_thread(_read)

    async def read_range(self, path: str, offset: int, length: int) -> bytes:
        def _read() -> bytes:
            with open(path, "rb") as f:
                f.seek(offset)
                return f.read(length)

        return await asyncio.to_thread(_read)

    async def write_bytes(self, path: str, data: bytes) -> None:
        def _write() -> None:
            with open(path, "wb") as f:
                f.write(data)

        await asyncio.to_thread(_write)

    async def metadata(self, path: str) -> FsStat:
        def _stat() -> FsStat:
            try:
                st = os.stat(path)
            except OSError:
                return FsStat(exists=False)
            return FsStat(
                exists=True,
                is_file=os.path.isfile(path),
                is_dir=os.path.isdir(path),
                size=st.st_size,
                mtime_ms=int(st.st_mtime * 1000),
            )

        return await asyncio.to_thread(_stat)

    async def list_dir(self, path: str) -> List[FsEntry]:
        def _list() -> List[FsEntry]:
            # os.listdir 对不存在/非目录原生抛 FileNotFoundError/
            # NotADirectoryError——协议语义零翻译
            entries: List[FsEntry] = []
            for name in os.listdir(path):
                full = os.path.join(path, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue  # 悬空软链等无法 stat 的条目跳过（对齐原 LsOperations）
                entries.append(
                    FsEntry(name=name, is_dir=stat_module.S_ISDIR(st.st_mode))
                )
            return entries

        return await asyncio.to_thread(_list)

    async def create_dir(self, path: str) -> None:
        await asyncio.to_thread(os.makedirs, path, exist_ok=True)

    async def walk(self, path: str, *, max_entries: int = 50_000) -> WalkResult:
        def _walk() -> WalkResult:
            if not os.path.exists(path):
                raise FileNotFoundError(path)
            items: List[WalkItem] = []
            truncated = False
            # 文件自身参与遍历：单文件路径 walk 即自身（grep/find 同语义）
            if os.path.isfile(path):
                return WalkResult(entries=(WalkItem(path=path, is_dir=False),))
            for root, dirs, files in os.walk(path):
                for name in dirs:
                    items.append(WalkItem(path=os.path.join(root, name), is_dir=True))
                for name in files:
                    items.append(WalkItem(path=os.path.join(root, name), is_dir=False))
                if len(items) >= max_entries:
                    truncated = True
                    items = items[:max_entries]
                    break
            return WalkResult(entries=tuple(items), truncated=truncated)

        return await asyncio.to_thread(_walk)

    async def check_writable(self, path: str) -> None:
        def _check() -> None:
            # 对齐原 EditOperations.access：不存在/只读在读与匹配之前就报错；
            # 异常带 errno（edit 工具的 _error_detail 透出 ENOENT/EACCES）
            if not os.path.exists(path):
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
            if not os.access(path, os.R_OK | os.W_OK):
                raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)

        await asyncio.to_thread(_check)


# 进程级单例（无状态——layer 只是方法集）
_local_layer: Optional[LocalFileSystemLayer] = None


def get_local_file_system_layer() -> LocalFileSystemLayer:
    """本地 layer 单例（工厂缺省注入用）。"""
    global _local_layer
    if _local_layer is None:
        _local_layer = LocalFileSystemLayer()
    return _local_layer
