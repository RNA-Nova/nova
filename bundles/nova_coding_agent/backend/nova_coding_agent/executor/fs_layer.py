"""远程 executor 文件系统层（``FileSystemLayer`` 的远程实现）。

``ExecutorClient.fs`` 的薄映射（executor 接入定案：六个 fs 工具经
``backend_file_layer`` 注入本层即切换为远程读写，实现体零分叉）：

- 路径以 ``file://`` URI 传递（与 process/cwd 同一 PathUri 形态）；
- ``metadata`` 不存在时回 ``FsStat(exists=False)``（远端报错归一为
  不存在——协议语义）；取消词汇例外直通（见下）；
- ``list_dir``/``walk`` 先做 metadata 前检，保证 ``FileNotFoundError``/
  ``NotADirectoryError`` 与本地层同语义（不解析远端错误字符串）；
- 遍历走 SDK 的 ``fs/walk``（服务端有界遍历——WalkOptions 上限）。

取消词汇：``signal`` 全方法透传 SDK fs 调用——在飞 RPC 的响应等待
被打断（AbortedError 直通，不收编成"不存在"之类的业务语义），预触发
在 SDK 举旗点快失败（不发请求）。收摊调用（``read_range`` finally 的
``fs/close``）有意不挂 signal：预触发快失败会把 close 掐死在发送前，
服务端句柄随连接干等泄漏（对位 SDK 收摊路径的 for_cleanup 纪律）。
"""

from __future__ import annotations

import errno
import os
from typing import List, Optional

from nova_async import AbortedError, AbortSignal
from nova_exec_server_client import ExecutorClient

from nova_coding_agent.tools_common.fs_layer import (
    FsEntry,
    FsStat,
    WalkItem,
    WalkResult,
)

_URI_PREFIX = "file://"


def _to_uri(path: str) -> str:
    return f"{_URI_PREFIX}{path}"


def _from_uri(uri: str) -> str:
    """walk 返回的 PathUri 剥回裸路径（供 fnmatch/PurePath/展示消费）。"""
    return uri[len(_URI_PREFIX) :] if uri.startswith(_URI_PREFIX) else uri


class ExecutorFileSystemLayer:
    """远程 executor fs 层（按 manager + url 构造；客户端经 manager 缓存复用）。"""

    def __init__(self, manager, url: str) -> None:
        self._manager = manager
        self._url = url

    async def _client(self) -> ExecutorClient:
        return await self._manager.get_client(self._url)

    async def read_bytes(
        self, path: str, *, signal: Optional[AbortSignal] = None
    ) -> bytes:
        client = await self._client()
        return await client.fs.read_file(_to_uri(path), signal=signal)

    async def read_range(
        self,
        path: str,
        offset: int,
        length: int,
        *,
        signal: Optional[AbortSignal] = None,
    ) -> bytes:
        client = await self._client()
        handle = await client.fs.open(_to_uri(path), signal=signal)
        try:
            data, _eof = await client.fs.read_block(
                handle, offset, length, signal=signal
            )
            return data
        finally:
            # 收摊不挂 signal（见模块 docstring——预触发快失败会掐死 close）
            await client.fs.close(handle)

    async def write_bytes(
        self, path: str, data: bytes, *, signal: Optional[AbortSignal] = None
    ) -> None:
        client = await self._client()
        await client.fs.write_file(_to_uri(path), data, signal=signal)

    async def metadata(
        self, path: str, *, signal: Optional[AbortSignal] = None
    ) -> FsStat:
        client = await self._client()
        try:
            meta = await client.fs.metadata(_to_uri(path), signal=signal)
        except AbortedError:
            # 取消词汇直通——收编成"不存在"会把用户取消误报成 FileNotFound
            raise
        except Exception:
            return FsStat(exists=False)
        return FsStat(
            exists=True,
            is_file=meta.is_file,
            is_dir=meta.is_directory,
            size=meta.size,
            mtime_ms=meta.modified_at_ms,
        )

    async def list_dir(
        self, path: str, *, signal: Optional[AbortSignal] = None
    ) -> List[FsEntry]:
        # metadata 前检：错误形态与本地层同语义（不解析远端错误字符串）
        stat = await self.metadata(path, signal=signal)
        if not stat.exists:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
        if not stat.is_dir:
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), path)
        client = await self._client()
        entries = await client.fs.read_dir(_to_uri(path), signal=signal)
        return [
            FsEntry(name=entry.file_name, is_dir=entry.is_directory)
            for entry in entries
        ]

    async def create_dir(
        self, path: str, *, signal: Optional[AbortSignal] = None
    ) -> None:
        client = await self._client()
        await client.fs.create_dir(_to_uri(path), recursive=True, signal=signal)

    async def walk(
        self,
        path: str,
        *,
        max_entries: int = 50_000,
        signal: Optional[AbortSignal] = None,
    ) -> WalkResult:
        from nova_exec_protocol import WalkOptions

        stat = await self.metadata(path, signal=signal)
        if not stat.exists:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
        # 文件自身参与遍历：单文件路径 walk 即自身（与本地层同语义）
        if stat.is_file:
            return WalkResult(entries=(WalkItem(path=path, is_dir=False),))
        client = await self._client()
        outcome = await client.fs.walk(
            _to_uri(path), WalkOptions(maxEntries=max_entries), signal=signal
        )
        items = tuple(
            WalkItem(path=_from_uri(entry.path), is_dir=entry.kind == "directory")
            for entry in outcome.entries
        )
        return WalkResult(entries=items, truncated=outcome.truncated)

    async def check_writable(
        self, path: str, *, signal: Optional[AbortSignal] = None
    ) -> None:
        stat = await self.metadata(path, signal=signal)
        if not stat.exists:
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
        # 远程元信息无权限位——写时自然报错（尽力而为，注释语义见协议文档）


_LAYER_CACHE: dict = {}


def get_executor_file_layer(manager, url: str) -> ExecutorFileSystemLayer:
    """按 url 缓存复用远程 fs 层（layer 无状态——client 缓存归 manager）。"""
    layer = _LAYER_CACHE.get(url)
    if layer is None:
        layer = ExecutorFileSystemLayer(manager, url)
        _LAYER_CACHE[url] = layer
    return layer


def reset_executor_file_layers() -> None:
    """清空 layer 缓存（测试隔离用）。"""
    _LAYER_CACHE.clear()
