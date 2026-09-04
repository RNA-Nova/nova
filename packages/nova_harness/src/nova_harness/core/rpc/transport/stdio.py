"""JSON-RPC over stdio transport implementation."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict, Optional

from nova_harness.core.rpc.transport.base import Transport

# 读侧行限：协议单帧为单行 JSON——带图 prompt（base64）/大会话全量历史
# 等合法帧轻松超过 asyncio 默认的 64KB，故显式放宽到 64MB（上限非预分配，
# 不超过不占内存；更大的载荷走临时文件路径引用，见 send_binary 注释）。
_READ_LIMIT = 64 * 1024 * 1024


class StdioTransport(Transport):
    """Read NDJSON from stdin and write NDJSON to stdout.

    This transport is used when Nova is spawned as a child process by a
    TUI or editor plugin. It does not support binary frames; large payloads
    should be written to temporary files and referenced by URL/path.

    写侧为异步 StreamWriter（connect_write_pipe 直绑 stdout fd）：
    - 大帧写入经 ``drain()`` 协作式让出事件循环——不冻结并发分派的其他
      task（同步 ``sys.stdout.flush()`` 在帧超管道缓冲（64KB）且读端慢时
      会原地阻塞整个循环，abort 应答被队头阻塞）；
    - 协议帧不经 ``sys.stdout`` 层——OutputGuard 的杂散重定向语义自动保持
      （依赖库 print 仍被拦到 stderr；协议帧直通 fd，无需白名单标记）。
    """

    def __init__(self) -> None:
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        # Windows 的 asyncio ProactorEventLoop 不支持 stdio 管道
        # （connect_read_pipe/connect_write_pipe 仅支持 POSIX fd）——
        # 该平台上读退化到执行器线程（阻塞 readline 不冻结事件循环），
        # 写直出（NDJSON 小帧，放弃 drain 协作，见 open 注释）
        self._win32_stdio = sys.platform == "win32"

    @property
    def supports_binary(self) -> bool:
        return False

    async def open(self) -> None:
        """Bind stdin to an async StreamReader and stdout to a StreamWriter."""
        if self._win32_stdio:
            return
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=_READ_LIMIT)
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        write_transport, write_protocol = await loop.connect_write_pipe(
            # FlowControlMixin 提供 drain() 所需的流控钩子（_drain_helper）——
            # 普通 Protocol 没有，超水位等待时会 AttributeError
            asyncio.streams.FlowControlMixin,
            sys.stdout,
        )
        self._writer = asyncio.StreamWriter(write_transport, write_protocol, None, loop)
        self._reader = reader

    async def read(self) -> Dict[str, Any] | None:
        """Read the next line from stdin and parse it as JSON."""
        if self._win32_stdio:
            loop = asyncio.get_running_loop()
            line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
            if not line:
                return None
            text = line.decode("utf-8").strip()
            if not text:
                return await self.read()
            return json.loads(text)
        if self._reader is None:
            raise RuntimeError("Transport not opened")
        line = await self._reader.readline()
        if not line:
            return None
        text = line.decode("utf-8").strip()
        if not text:
            return await self.read()
        return json.loads(text)

    async def write(self, msg: Dict[str, Any]) -> None:
        """Write a JSON object as a single line to stdout (async, backpressured)."""
        if self._win32_stdio:
            sys.stdout.buffer.write(
                json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n"
            )
            sys.stdout.buffer.flush()
            return
        if self._writer is None:
            raise RuntimeError("Transport not opened")
        self._writer.write(json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n")
        # 缓冲超水位时让出事件循环（协作式等待读端消费），而非阻塞冻结
        await self._writer.drain()

    async def send_binary(
        self, data: bytes, metadata: Dict[str, Any] | None = None
    ) -> None:
        """stdio does not support binary frames."""
        raise NotImplementedError("StdioTransport does not support binary frames")

    async def receive_binary(self) -> tuple[bytes, Dict[str, Any] | None] | None:
        """stdio does not support binary frames."""
        raise NotImplementedError("StdioTransport does not support binary frames")

    async def close(self) -> None:
        """Close stdin reader and stdout writer."""
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        if self._reader is not None:
            self._reader.feed_eof()
            self._reader = None
