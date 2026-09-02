"""文件系统管理"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .errors import FileSystemError
from .protocol import (
    FS_CANONICALIZE,
    FS_CLOSE,
    FS_COPY,
    FS_CREATE_DIRECTORY,
    FS_GET_METADATA,
    FS_OPEN,
    FS_READ_BLOCK,
    FS_READ_DIRECTORY,
    FS_READ_FILE,
    FS_READ_STREAM,
    FS_READ_STREAM_CHUNK,
    FS_READ_STREAM_DONE,
    FS_REMOVE,
    FS_WALK,
    FS_WRITE_FILE,
    DirEntry,
    FileMetadata,
    FsCanonicalizeParams,
    FsCanonicalizeResponse,
    FsCloseParams,
    FsCopyParams,
    FsCreateDirectoryParams,
    FsGetMetadataParams,
    FsOpenParams,
    FsOpenResponse,
    FsReadBlockParams,
    FsReadBlockResponse,
    FsReadDirectoryParams,
    FsReadDirectoryResponse,
    FsReadFileParams,
    FsReadFileResponse,
    FsReadStreamChunkNotification,
    FsReadStreamDoneNotification,
    FsReadStreamParams,
    FsReadStreamResponse,
    FsRemoveParams,
    FsWalkParams,
    FsWriteFileParams,
    WalkOptions,
    WalkOutcome,
)
from .transport import WebSocketTransport


@dataclass
class ReadStreamHandle:
    """流式读取句柄"""

    handle_id: str
    client: FileSystemManager


class FileSystemManager:
    """文件系统管理器"""

    def __init__(self, transport: WebSocketTransport):
        self._transport = transport
        self._stream_queues: dict[str, asyncio.Queue] = {}
        self._stream_dones: dict[str, FsReadStreamDoneNotification] = {}
        transport.on_notification(self._handle_stream_notification)

    async def _handle_stream_notification(self, message: dict) -> None:
        """处理流式读取通知"""
        method = message.get("method")
        params = message.get("params", {})
        handle_id = params.get("handleId")

        if method == FS_READ_STREAM_CHUNK:
            notification = FsReadStreamChunkNotification.model_validate(params)
            queue = self._stream_queues.get(handle_id)
            if queue:
                await queue.put(notification)
        elif method == FS_READ_STREAM_DONE:
            notification = FsReadStreamDoneNotification.model_validate(params)
            self._stream_dones[handle_id] = notification
            queue = self._stream_queues.get(handle_id)
            if queue:
                await queue.put(None)  # 结束标记

    async def read_file(self, path: str) -> bytes:
        """读取文件（小文件推荐）"""
        params = FsReadFileParams(path=path)
        result = await self._transport.send_request(
            FS_READ_FILE, params.model_dump(by_alias=True)
        )
        response = FsReadFileResponse.model_validate(result)
        return response.data

    async def read_stream(
        self,
        path: str,
        block_size: int = 256 * 1024,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[bytes]:
        """流式读取文件（大文件推荐）"""
        handle_id = f"s-{id(self) % 10000}-{int(asyncio.get_event_loop().time() * 1000) % 1000000}"
        queue: asyncio.Queue = asyncio.Queue()
        self._stream_queues[handle_id] = queue

        params = FsReadStreamParams(
            handleId=handle_id,
            path=path,
            offset=offset,
            len=length,
            blockSize=block_size,
        )
        result = await self._transport.send_request(
            FS_READ_STREAM, params.model_dump(by_alias=True)
        )
        FsReadStreamResponse.model_validate(result)

        try:
            while True:
                notification = await queue.get()
                if notification is None:
                    break
                yield notification.chunk
                if notification.eof:
                    break
        finally:
            self._stream_queues.pop(handle_id, None)
            self._stream_dones.pop(handle_id, None)

    async def write_file(self, path: str, data: bytes) -> None:
        """写入文件"""
        import base64

        params = FsWriteFileParams(
            path=path,
            dataBase64=base64.b64encode(data).decode(),
        )
        await self._transport.send_request(
            FS_WRITE_FILE, params.model_dump(by_alias=True)
        )

    async def read_dir(self, path: str) -> list[DirEntry]:
        """列出目录"""
        params = FsReadDirectoryParams(path=path)
        result = await self._transport.send_request(
            FS_READ_DIRECTORY, params.model_dump(by_alias=True)
        )
        response = FsReadDirectoryResponse.model_validate(result)
        return response.entries

    async def walk(self, path: str, options: WalkOptions | None = None) -> WalkOutcome:
        """目录遍历（界限经 WalkOptions——深度/目录数/条目数上限）"""
        params = FsWalkParams(path=path, options=options or WalkOptions())
        result = await self._transport.send_request(
            FS_WALK, params.model_dump(by_alias=True)
        )
        return WalkOutcome.model_validate(result)

    async def create_dir(self, path: str, recursive: bool = True) -> None:
        """创建目录"""
        params = FsCreateDirectoryParams(path=path, recursive=recursive)
        await self._transport.send_request(
            FS_CREATE_DIRECTORY, params.model_dump(by_alias=True)
        )

    async def remove(
        self, path: str, recursive: bool = True, force: bool = False
    ) -> None:
        """删除文件或目录"""
        params = FsRemoveParams(path=path, recursive=recursive, force=force)
        await self._transport.send_request(FS_REMOVE, params.model_dump(by_alias=True))

    async def copy(self, src: str, dst: str, recursive: bool = False) -> None:
        """复制文件或目录"""
        params = FsCopyParams(
            sourcePath=src,
            destinationPath=dst,
            recursive=recursive,
        )
        await self._transport.send_request(FS_COPY, params.model_dump(by_alias=True))

    async def metadata(self, path: str) -> FileMetadata:
        """获取文件元数据"""
        params = FsGetMetadataParams(path=path)
        result = await self._transport.send_request(
            FS_GET_METADATA, params.model_dump(by_alias=True)
        )
        return FileMetadata.model_validate(result)

    async def canonicalize(self, path: str) -> str:
        """规范化路径"""
        params = FsCanonicalizeParams(path=path)
        result = await self._transport.send_request(
            FS_CANONICALIZE, params.model_dump(by_alias=True)
        )
        response = FsCanonicalizeResponse.model_validate(result)
        return response.path

    # 分块读取 API（兼容旧版）
    async def open(self, path: str, handle_id: str | None = None) -> str:
        """打开文件用于分块读取"""
        handle_id = handle_id or f"block-{id(self)}-{asyncio.get_event_loop().time()}"
        params = FsOpenParams(handleId=handle_id, path=path)
        result = await self._transport.send_request(
            FS_OPEN, params.model_dump(by_alias=True)
        )
        response = FsOpenResponse.model_validate(result)
        return response.handle_id

    async def read_block(
        self, handle_id: str, offset: int, length: int
    ) -> tuple[bytes, bool]:
        """分块读取"""
        params = FsReadBlockParams(handleId=handle_id, offset=offset, len=length)
        result = await self._transport.send_request(
            FS_READ_BLOCK, params.model_dump(by_alias=True)
        )
        response = FsReadBlockResponse.model_validate(result)
        return response.chunk, response.eof

    async def close(self, handle_id: str) -> None:
        """关闭分块读取句柄"""
        params = FsCloseParams(handleId=handle_id)
        await self._transport.send_request(FS_CLOSE, params.model_dump(by_alias=True))
