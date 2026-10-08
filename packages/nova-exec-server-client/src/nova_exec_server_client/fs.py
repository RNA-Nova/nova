"""文件系统管理"""

from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable

from nova_protocol import (
    ENVIRONMENT_INFO,
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
    FS_WRITE_BLOCK,
    FS_WRITE_FILE,
    FS_WRITE_STREAM,
    FS_WRITE_STREAM_CHUNK,
    FS_WRITE_STREAM_DONE,
    MAX_WRITE_STREAM_CHUNK_BYTES,
    DirEntry,
    EnvironmentInfo,
    FileMetadata,
    FsCanonicalizeParams,
    FsCanonicalizeResponse,
    FsCloseParams,
    FsCopyParams,
    FsCreateDirectoryParams,
    FsGetMetadataParams,
    FsOpenMode,
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
    FsWriteBlockParams,
    FsWriteBlockResponse,
    FsWriteFileParams,
    FsWriteStreamChunkNotification,
    FsWriteStreamDoneParams,
    FsWriteStreamDoneResponse,
    FsWriteStreamParams,
    FsWriteStreamResponse,
    WalkOptions,
    WalkOutcome,
)

from .errors import FileSystemError, ProtocolError, TimeoutError, TransportError
from .notifications import NotificationRouter, ReadStreamEvent
from .pool import CHANNEL_DATA
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

    `environment_info`：环境元数据提供方（client 装配时注入
    ExecutorClient.environment_info，共享 initialize 捎带/惰性拉取的缓存）——
    fileWriteStreaming 能力门（对位 codex e7798c9944）经它查询；
    独立使用时缺省为 None，按需经 transport 惰性拉取一次并本地缓存。
    """

    def __init__(
        self,
        transport: Transport,
        router: NotificationRouter | None = None,
        environment_info: Callable[[], Awaitable[EnvironmentInfo]] | None = None,
    ):
        self._transport = transport
        if router is None:
            router = NotificationRouter()
            transport.on_notification(router.dispatch)
        self._router = router
        self._environment_info_provider = environment_info
        self._environment_info_cached: EnvironmentInfo | None = None

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
        *,
        sandbox: dict | None = None,
    ) -> AsyncIterator[bytes]:
        """流式读取文件（大文件推荐）

        `sandbox`（v1.10 起服务端按请求沙箱开门，能力位
        sandboxedFileStreaming）：FileSystemSandboxContext 的线上 dict，
        语义同 `open()`；None = 无上下文直开（现状语义）。

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
            sandbox=sandbox,
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
        offset: int | None = None,
        *,
        sandbox: dict | None = None,
    ) -> int:
        """流式写入文件（大文件推荐），返回实际落盘总字节数。

        与 read_stream 方向对偶（客户端分片推，服务端按 seq 严格序落盘）：

        - `chunks`：字节源（同步/异步可迭代），每片再按 `block_size` 切块；
          整块 `bytes` 请直接用 `write_file`
        - `offset`（v1.12 断点续传）：None = 创建/截断（现状语义）；
          Some(n) = 不截断、从 n 续写（n 不得超当前文件长度，服务端
          越界/缺失文件即拒）
        - `sandbox`（v1.10 起服务端按请求沙箱开门写，能力位
          sandboxedFileStreaming）：FileSystemSandboxContext 的线上 dict；
          None = 无上下文直开（现状语义）
        - 协议序列：`fs/writeStream` 请求开句柄 → `fs/writeStream/chunk`
          通知（seq 从 0 连续）→ 空块 `eof=True` 收尾 → `fs/writeStream/done`
          请求确认（done.totalBytes 为全量语义：offset 起点 + 本次流式字节数）
        - 背压：chunk 通知逐条 await 写线（drain），服务端读慢时发送方
          挂起而非内存膨胀
        - 中断语义（v1.12 翻转）：服务端中止/断连不再删半成品——文件留在
          盘上可续传；本地异常时客户端发 `fs/close` 主动中止（须走数据面
          通道——句柄状态随连接）；chunk 通知无回执，服务端业务错误
          （乱序/超限/写盘失败）留到 done 回报，这里转为 FileSystemError
        """
        if isinstance(chunks, (bytes, bytearray, memoryview)):
            raise TypeError("chunks 需为字节迭代器；整块字节请用 write_file")
        if not 1 <= block_size <= MAX_WRITE_STREAM_CHUNK_BYTES:
            raise ValueError(
                f"block_size 须在 1..{MAX_WRITE_STREAM_CHUNK_BYTES} 之间"
                f"（服务端单块上限），收到 {block_size}"
            )

        handle_id = _new_handle_id("w")
        params = FsWriteStreamParams(
            handleId=handle_id, path=path, offset=offset, sandbox=sandbox
        )
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
            # 中止：服务端对写流句柄的 fs/close 即 abort（v1.12 起不再删半成品——
            # 文件留在盘上可续传）；连接已死时尽力而为
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
            # 服务端把流业务错误留到 done 回报（v1.12 起半成品留在盘上）
            raise FileSystemError(f"write stream `{handle_id}` failed: {e}") from e
        response = FsWriteStreamDoneResponse.model_validate(result)
        return response.total_bytes

    async def write_stream_resumable(
        self,
        path: str,
        data_source: (
            bytes | bytearray | memoryview | AsyncIterable[bytes] | Iterable[bytes]
        ),
        *,
        block_size: int = 256 * 1024,
        offset: int = 0,
        max_resume_attempts: int = 3,
        sandbox: dict | None = None,
    ) -> int:
        """断点续传的流式写入（v1.12，nova 自有通道）：断线后重连续传。

        流程：记账已确认字节 → 断线时经 getMetadata 对账服务端文件大小 →
        带 `offset` 重开流续传（源从头重迭代、跳过已确认前缀）→ done 对账
        totalBytes（全量语义：续传起点 + 本轮推送字节数，与服务端一致才返回）。

        - `data_source`：`bytes` 或可重复迭代（`__iter__` 每次产新迭代器）的
          字节源；一次性迭代器/生成器在需要续传时无法重放——将以
          FileSystemError 明确报错而非静默产出错数据
        - `offset`：初始续传起点（0 = 新上传，创建/截断）
        - `max_resume_attempts`：断线后续传的最大重试次数（传输层恢复基建
          （ManagedTransport）兜不住时的兜底轮次）
        - `sandbox`：语义同 `write_stream`；续传重开时同样透传
        - 返回服务端确认的全量字节数；done 对账不符或服务端文件缩水即
          FileSystemError
        """
        confirmed = offset
        attempts = 0
        while True:
            chunks = _TallyingSource(_replay_bytes_from(data_source, confirmed))
            try:
                total = await self.write_stream(
                    path,
                    chunks,
                    block_size=block_size,
                    offset=confirmed if confirmed > 0 else None,
                    sandbox=sandbox,
                )
            except (TransportError, TimeoutError) as err:
                attempts += 1
                if attempts > max_resume_attempts:
                    raise FileSystemError(
                        f"write stream `{path}` failed after {attempts} resume attempts: {err}"
                    ) from err
                # 续传要重放源——一次性迭代器/生成器无法重放，明确报错而非
                # 静默产出错数据
                _ensure_replayable(data_source)
                # getMetadata 对账服务端文件大小（断线窗口内推送丢失由此兜底）
                confirmed = await self._reconcile_write_offset(path, confirmed)
                continue
            # done 对账：全量字节数 = 本轮起点 + 本轮推送
            if total != confirmed + chunks.count:
                raise FileSystemError(
                    f"write stream reconciliation failed: server confirmed "
                    f"{total} bytes, client pushed {chunks.count} bytes "
                    f"from offset {confirmed}"
                )
            return total

    async def read_stream_resumable(
        self,
        path: str,
        block_size: int = 256 * 1024,
        offset: int = 0,
        length: int | None = None,
        max_resume_attempts: int = 3,
        *,
        sandbox: dict | None = None,
    ) -> AsyncIterator[bytes]:
        """断点续传的流式读取（v1.12，nova 自有通道）：断线后重连续读。

        读侧无服务端可变状态——记账已收字节，断线后带 offset 重开流续读；
        每轮 done 对账该轮字节数（read_stream 内建校验），跨轮游标连续。
        仅对传输截断（done 字节数不符）与连接/超时错误续传；服务端读失败
        （done 携带 error，如权限拒绝）原样抛出不重试。

        `sandbox`：语义同 `read_stream`；重开 readStream 续读时同样透传。
        """
        produced = 0
        attempts = 0
        while True:
            remaining = None if length is None else length - produced
            if remaining is not None and remaining <= 0:
                return
            try:
                async for chunk in self.read_stream(
                    path,
                    block_size=block_size,
                    offset=offset + produced,
                    length=remaining,
                    sandbox=sandbox,
                ):
                    produced += len(chunk)
                    yield chunk
                return
            except (TransportError, TimeoutError) as err:
                attempts = self._resume_attempt_or_raise(
                    path, attempts, max_resume_attempts, err
                )
            except FileSystemError as err:
                # 只对传输形态的错误续传：done 字节不符（"incomplete"——丢块/
                # 断线截断）与连接失败清扫（"failed to resume"/"disconnected"）。
                # 服务端真实读失败（done 携带 error，如权限拒绝）原样抛出不重试。
                text = str(err)
                if not (
                    "incomplete" in text
                    or "failed to resume" in text
                    or "disconnected" in text
                    or "transport closed" in text
                ):
                    raise
                attempts = self._resume_attempt_or_raise(
                    path, attempts, max_resume_attempts, err
                )

    async def _reconcile_write_offset(self, path: str, confirmed: int) -> int:
        """断线后对账服务端文件大小：文件缩水（服务端状态回退）即对账失败。"""
        try:
            metadata = await self.metadata(path)
        except ProtocolError as err:
            if err.code == -32004:
                # NotFound：开门都没成功——从头再来
                if confirmed == 0:
                    return 0
                raise FileSystemError(
                    f"write stream reconciliation failed: server file `{path}` "
                    f"vanished (client had {confirmed} confirmed bytes)"
                ) from err
            raise
        size = int(metadata.size)
        if size < confirmed:
            raise FileSystemError(
                f"write stream reconciliation failed: server file `{path}` shrank "
                f"from {confirmed} to {size} bytes"
            )
        return size

    @staticmethod
    def _resume_attempt_or_raise(
        path: str, attempts: int, max_resume_attempts: int, err: Exception
    ) -> int:
        attempts += 1
        if attempts > max_resume_attempts:
            raise FileSystemError(
                f"read stream `{path}` failed after {attempts} resume attempts: {err}"
            ) from err
        return attempts

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

    # 随机访问句柄 API（fs/open|readBlock|writeBlock|close）
    async def open(
        self,
        path: str,
        handle_id: str | None = None,
        *,
        mode: FsOpenMode = FsOpenMode.READ,
        sandbox: dict | None = None,
    ) -> str:
        """打开文件句柄用于分块读/写

        `mode=FsOpenMode.REPLACE`：写打开（文件缺失则创建、存在则截断），
        随后用 `write_block` 按显式 offset 定位写。旧 executor 会把 replace
        静默降级为只读句柄——能力门（对位 codex e7798c9944）在
        fileWriteStreaming=false 时本地报错，不发线上请求。
        """
        if (
            mode is FsOpenMode.REPLACE
            and not await self._file_write_streaming_supported()
        ):
            raise ProtocolError("exec-server does not support writable file streams")
        handle_id = handle_id or _new_handle_id("b")
        params = FsOpenParams(handleId=handle_id, path=path, mode=mode, sandbox=sandbox)
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

    async def write_block(self, handle_id: str, offset: int, chunk: bytes) -> None:
        """定位写块（对位 RS `fs_write_block`）：非空块、解码后 ≤1MiB，显式 offset。

        成功返回即确认全部字节落盘；能力门同上（fileWriteStreaming=false 时
        本地报错，不发线上请求）。
        """
        if not await self._file_write_streaming_supported():
            raise ProtocolError("exec-server does not support writable file streams")
        params = FsWriteBlockParams(handleId=handle_id, offset=offset, chunk=chunk)
        result = await self._transport.send_request(
            FS_WRITE_BLOCK, params.model_dump(by_alias=True)
        )
        FsWriteBlockResponse.model_validate(result)

    async def close(self, handle_id: str) -> None:
        """关闭随机访问句柄（读/写通用）"""
        params = FsCloseParams(handleId=handle_id)
        await self._transport.send_request(FS_CLOSE, params.model_dump(by_alias=True))

    async def _file_write_streaming_supported(self) -> bool:
        """fileWriteStreaming 能力位（对位 codex e7798c9944 的客户端门）：
        优先走注入的环境元数据缓存；独立使用时惰性拉取一次并本地缓存。
        capabilities 缺失（旧服务端）按 false 处理。"""
        if self._environment_info_provider is not None:
            info = await self._environment_info_provider()
        else:
            if self._environment_info_cached is None:
                result = await self._transport.send_request(ENVIRONMENT_INFO)
                self._environment_info_cached = EnvironmentInfo.model_validate(result)
            info = self._environment_info_cached
        capabilities = info.capabilities
        return capabilities is not None and capabilities.file_write_streaming


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


class _TallyingSource:
    """记账包装：统计流过的字节总数（write_stream_resumable 的 done 对账用）。"""

    def __init__(self, inner):
        self._inner = inner
        self.count = 0

    async def __aiter__(self):
        async for piece in _iterate_bytes(self._inner):
            self.count += len(piece)
            yield piece


async def _replay_bytes_from(
    source: bytes | bytearray | memoryview | AsyncIterable[bytes] | Iterable[bytes],
    skip: int,
) -> AsyncIterator[bytes]:
    """从源起点重放并跳过前 `skip` 字节（断点续传的源侧游标）。

    bytes 族直接切片；可重复迭代容器从头重迭代后跳过（可重放性由
    `_ensure_replayable` 在续传前把关）。
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
        if skip > len(data):
            raise FileSystemError(
                f"resume offset {skip} exceeds the source length {len(data)}"
            )
        yield data[skip:]
        return
    skipped = 0
    async for piece in _iterate_bytes(source):
        if skipped + len(piece) <= skip:
            skipped += len(piece)
            continue
        yield piece[skip - skipped :]
        skipped = skip


def _ensure_replayable(
    source: bytes | bytearray | memoryview | AsyncIterable[bytes] | Iterable[bytes],
) -> None:
    """续传要重放源——裸迭代器/生成器（iter()/__aiter__() 返回自身）无法重放：
    重迭代会静默变空或错位，明确报错而非静默产出错数据。"""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return
    if hasattr(source, "__iter__") and iter(source) is not source:
        return
    if hasattr(source, "__aiter__") and source.__aiter__() is not source:
        return
    raise FileSystemError(
        "write_stream_resumable 的数据源无法重放（一次性迭代器/生成器）——"
        "断线续传需要 bytes 或可重复迭代容器"
    )
