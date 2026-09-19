"""文件系统管理"""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass

from .errors import FileSystemError, ProtocolError
from .notifications import NotificationRouter, ReadStreamEvent
from .pool import CHANNEL_DATA
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
    FS_REMOVE,
    FS_WALK,
    FS_WRITE_FILE,
    FS_WRITE_STREAM,
    FS_WRITE_STREAM_CHUNK,
    FS_WRITE_STREAM_DONE,
    MAX_WRITE_STREAM_CHUNK_BYTES,
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
    FsReadStreamParams,
    FsReadStreamResponse,
    FsRemoveParams,
    FsWalkParams,
    FsWriteFileParams,
    FsWriteStreamChunkNotification,
    FsWriteStreamDoneParams,
    FsWriteStreamDoneResponse,
    FsWriteStreamParams,
    FsWriteStreamResponse,
    WalkOptions,
    WalkOutcome,
)
from .transport import Transport


def _new_handle_id(prefix: str) -> str:
    """生成 fs 句柄 id：uuid4 随机（取代 id() 对象地址复用 + 时间戳模数的
    碰撞面），截断保持全长 <= 32 字节（服务端 MAX_FILE_READ/WRITE_HANDLE_ID_BYTES
    上限；120bit 随机量对会话内句柄足够）"""
    return f"{prefix}-{uuid.uuid4().hex[:30]}"


class FileSystemManager:
    """文件系统管理器

    `router`：统一通知分发器（notifications.NotificationRouter）——
    client 装配时注入（全局单例，传输层通知统一经它按 handle_id 路由）；
    独立使用时缺省自建并自挂到 transport.on_notification（旧行为兼容）。
    """

    def __init__(self, transport: Transport, router: NotificationRouter | None = None):
        self._transport = transport
        if router is None:
            router = NotificationRouter()
            transport.on_notification(router.dispatch)
        self._router = router

    def _stream_channel(self, method: str) -> str | None:
        """解析流式方法的落点通道名（池化传输打标签用；裸传输无此概念归 None）"""
        resolve = getattr(self._transport, "resolve_channel", None)
        return resolve(method) if resolve is not None else None

    async def read_file(self, path: str, follow_symlinks: bool | None = None) -> bytes:
        """读取文件（小文件推荐）

        `follow_symlinks=False` 时逐组件拒绝穿越符号链接（no-follow 语义）；
        None 不下发该字段，服务端按默认 true（旧行为）处理。
        """
        params = FsReadFileParams(path=path, followSymlinks=follow_symlinks)
        result = await self._transport.send_request(
            FS_READ_FILE, params.model_dump(by_alias=True, exclude_none=True)
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
        """流式读取文件（大文件推荐）

        协议序列：先注册推送路由再发 `fs/readStream` 请求（服务端响应后即开始
        推送，注册不能比推送晚到）→ 逐块收 `fs/readStream/chunk` →
        `fs/readStream/done` 收尾。

        收尾校验（done 语义，缺一即 FileSystemError）：
        - done 携带 error：服务端读失败（旧版静默吞掉的缺陷，已修）
        - done 的 totalBytes 与实际收齐字节数不等：传输丢块/断线截断
          （连接恢复窗口内推送丢失由此兜底，不静默产出截断数据）

        背压：每流队列上限 READ_STREAM_QUEUE_CAPACITY 块——消费过慢宁可
        断流报错（FileSystemError），不阻塞连接级通知分发（对位 Rust 的
        try_send 断流语义）。
        """
        handle_id = _new_handle_id("s")
        queue = self._router.register_stream(
            handle_id, channel=self._stream_channel(FS_READ_STREAM)
        )

        params = FsReadStreamParams(
            handleId=handle_id,
            path=path,
            offset=offset,
            len=length,
            blockSize=block_size,
        )
        try:
            result = await self._transport.send_request(
                FS_READ_STREAM, params.model_dump(by_alias=True)
            )
        except Exception:
            # 开流失败即注销路由（对位 Rust open_push 的失败清理）
            self._router.unregister_stream(handle_id)
            raise
        FsReadStreamResponse.model_validate(result)

        received = 0
        try:
            while True:
                event: ReadStreamEvent = await queue.get()
                if event.kind == "failed":
                    raise FileSystemError(
                        f"read stream `{handle_id}` failed: {event.error}"
                    )
                if event.kind == "done":
                    if received != event.total_bytes:
                        raise FileSystemError(
                            f"read stream `{handle_id}` incomplete: received "
                            f"{received} bytes, server sent {event.total_bytes}"
                        )
                    break
                received += len(event.chunk)
                yield event.chunk
        finally:
            self._router.unregister_stream(handle_id)

    async def write_file(
        self, path: str, data: bytes, follow_symlinks: bool | None = None
    ) -> None:
        """写入文件；`follow_symlinks` 语义同 `read_file`（None=服务端默认 true）"""
        params = FsWriteFileParams(
            path=path,
            dataBase64=base64.b64encode(data).decode(),
            followSymlinks=follow_symlinks,
        )
        await self._transport.send_request(
            FS_WRITE_FILE, params.model_dump(by_alias=True, exclude_none=True)
        )

    async def write_stream(
        self,
        path: str,
        chunks: AsyncIterable[bytes] | Iterable[bytes],
        block_size: int = 256 * 1024,
    ) -> int:
        """流式写入文件（大文件推荐），返回实际落盘总字节数。

        与 read_stream 方向对偶（客户端分片推，服务端按 seq 严格序落盘）：

        - `chunks`：字节源（同步/异步可迭代），每片再按 `block_size` 切块；
          整块 `bytes` 请直接用 `write_file`
        - 协议序列：`fs/writeStream` 请求开句柄（打开即创建/截断）→
          `fs/writeStream/chunk` 通知（seq 从 0 连续）→ 空块 `eof=True` 收尾 →
          `fs/writeStream/done` 请求确认
        - 背压：chunk 通知逐条 await 写线（drain），服务端读慢时发送方
          挂起而非内存膨胀
        - 中断语义：服务端中断不产生可见文件（中止/断连/乱序删半截）。
          本地异常时客户端发 `fs/close` 主动中止（须走数据面通道——句柄
          状态随连接）；chunk 通知无回执，服务端业务错误（乱序/超限/写盘
          失败）留到 done 回报，这里转为 FileSystemError
        """
        if isinstance(chunks, (bytes, bytearray, memoryview)):
            raise TypeError("chunks 需为字节迭代器；整块字节请用 write_file")
        if not 1 <= block_size <= MAX_WRITE_STREAM_CHUNK_BYTES:
            raise ValueError(
                f"block_size 须在 1..{MAX_WRITE_STREAM_CHUNK_BYTES} 之间"
                f"（服务端单块上限），收到 {block_size}"
            )

        handle_id = _new_handle_id("w")
        params = FsWriteStreamParams(handleId=handle_id, path=path)
        result = await self._transport.send_request(
            FS_WRITE_STREAM, params.model_dump(by_alias=True)
        )
        FsWriteStreamResponse.model_validate(result)

        seq = 0
        try:
            async for piece in _iterate_bytes(chunks):
                for offset in range(0, len(piece), block_size):
                    notification = FsWriteStreamChunkNotification(
                        handleId=handle_id,
                        seq=seq,
                        chunk=piece[offset : offset + block_size],
                        eof=False,
                    )
                    await self._transport.send_notification(
                        FS_WRITE_STREAM_CHUNK,
                        notification.model_dump(by_alias=True),
                    )
                    seq += 1
            # 空块 eof=True 收尾（服务端状态机要求见过 eof 块才接受 done）
            await self._transport.send_notification(
                FS_WRITE_STREAM_CHUNK,
                FsWriteStreamChunkNotification(
                    handleId=handle_id, seq=seq, chunk=b"", eof=True
                ).model_dump(by_alias=True),
            )
        except Exception:
            # 中止：服务端对写流句柄的 fs/close 即 abort（删除半截文件）；
            # 连接已死时尽力而为
            try:
                await self._transport.send_request(
                    FS_CLOSE,
                    FsCloseParams(handleId=handle_id).model_dump(by_alias=True),
                    channel=CHANNEL_DATA,
                )
            except Exception:
                pass
            raise

        try:
            result = await self._transport.send_request(
                FS_WRITE_STREAM_DONE,
                FsWriteStreamDoneParams(handleId=handle_id).model_dump(by_alias=True),
            )
        except ProtocolError as e:
            # 服务端把流业务错误留到 done 回报（半截文件已删）
            raise FileSystemError(f"write stream `{handle_id}` failed: {e}") from e
        response = FsWriteStreamDoneResponse.model_validate(result)
        return response.total_bytes

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

    async def create_dir(
        self, path: str, recursive: bool = True, follow_symlinks: bool | None = None
    ) -> None:
        """创建目录；`follow_symlinks` 语义同 `read_file`（None=服务端默认 true）"""
        params = FsCreateDirectoryParams(
            path=path, recursive=recursive, followSymlinks=follow_symlinks
        )
        await self._transport.send_request(
            FS_CREATE_DIRECTORY, params.model_dump(by_alias=True, exclude_none=True)
        )

    async def remove(
        self,
        path: str,
        recursive: bool = True,
        force: bool = False,
        follow_symlinks: bool | None = None,
    ) -> None:
        """删除文件或目录；`follow_symlinks` 语义同 `read_file`（None=服务端默认 true）。

        注意：no-follow（False）不支持递归删除（服务端报 Unsupported）。
        """
        params = FsRemoveParams(
            path=path, recursive=recursive, force=force, followSymlinks=follow_symlinks
        )
        await self._transport.send_request(
            FS_REMOVE, params.model_dump(by_alias=True, exclude_none=True)
        )

    async def copy(self, src: str, dst: str, recursive: bool = False) -> None:
        """复制文件或目录"""
        params = FsCopyParams(
            sourcePath=src,
            destinationPath=dst,
            recursive=recursive,
        )
        await self._transport.send_request(FS_COPY, params.model_dump(by_alias=True))

    async def metadata(
        self, path: str, follow_symlinks: bool | None = None
    ) -> FileMetadata:
        """获取文件元数据；`follow_symlinks` 语义同 `read_file`（None=服务端默认 true）"""
        params = FsGetMetadataParams(path=path, followSymlinks=follow_symlinks)
        result = await self._transport.send_request(
            FS_GET_METADATA, params.model_dump(by_alias=True, exclude_none=True)
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
        handle_id = handle_id or _new_handle_id("b")
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


async def _iterate_bytes(
    chunks: AsyncIterable[bytes] | Iterable[bytes],
) -> AsyncIterator[bytes]:
    """统一同步/异步字节源为异步迭代（bytearray/memoryview 归一为 bytes）"""
    if hasattr(chunks, "__aiter__"):
        async for piece in chunks:
            yield bytes(piece)
    else:
        for piece in chunks:
            yield bytes(piece)
