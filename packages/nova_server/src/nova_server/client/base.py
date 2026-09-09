"""客户端多路复用基类（传输无关）。

把 JSON-RPC 客户端的共性抽到这里——**id 多路复用 + 有序事件队列 +
读泵**；具体传输（进程内 MemoryTransport 对端、WebSocket 远程）各自
实现 :class:`FrameChannel` 三原语（send/recv/close）后挂上来。

对位 codex ``app-server-client`` 的公共骨架：InProcess 与 Remote 两个
变体共享同一套请求/事件语义。
"""

from __future__ import annotations

import asyncio
import itertools
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, Optional

__all__ = ["FrameChannel", "MultiplexedClient", "ProtocolError"]

_CLOSE_SENTINEL: Optional[Dict[str, Any]] = None


class ProtocolError(RuntimeError):
    """请求收到 JSON-RPC error 帧（code/message/data 原样携带）。"""

    def __init__(self, code: Any, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[{code}] {message}")


class FrameChannel(ABC):
    """帧通道三原语：客户端唯一需要传输层提供的能力。"""

    @abstractmethod
    async def send_frame(self, frame: Dict[str, Any]) -> None:
        """发送一帧 JSON-RPC 消息。"""

    @abstractmethod
    async def recv_frame(self) -> Optional[Dict[str, Any]]:
        """接收下一帧；通道关闭（EOF）返回 ``None``。"""

    @abstractmethod
    async def close_channel(self) -> None:
        """关闭通道（幂等）。"""


class MultiplexedClient:
    """多路复用客户端：请求按 id 路由响应，通知帧入有序事件队列。

    对位 codex 客户端 worker 的设计——事件队列只此一个消费者（保序），
    请求等待不阻塞事件流转。
    """

    def __init__(self, channel: FrameChannel) -> None:
        self._channel = channel
        self._ids = itertools.count(1)
        self._pending: Dict[Any, "asyncio.Future[Dict[str, Any]]"] = {}
        self._events: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()
        self._reader: Optional["asyncio.Task[None]"] = None
        self._closed = False

    # ------------------------------------------------------------------
    # 请求 / 通知
    # ------------------------------------------------------------------

    async def request(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """发送 JSON-RPC 请求并返回 result；error 帧抛 :class:`ProtocolError`。"""
        request_id = next(self._ids)
        frame: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            frame["params"] = params

        future: "asyncio.Future[Dict[str, Any]]" = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        try:
            await self._channel.send_frame(frame)
            response = await future
        finally:
            self._pending.pop(request_id, None)

        if "error" in response:
            error = response["error"]
            raise ProtocolError(
                error.get("code"), error.get("message", ""), error.get("data")
            )
        return response.get("result")

    async def notify(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> None:
        """发送通知（无 id，fire-and-forget）。"""
        frame: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            frame["params"] = params
        await self._channel.send_frame(frame)

    # ------------------------------------------------------------------
    # 事件流（服务端推送，有序）
    # ------------------------------------------------------------------

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        """按序产出服务端通知帧，直到连接关闭。"""
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def next_event(self) -> Optional[Dict[str, Any]]:
        """取下一条服务端通知；连接关闭后返回 ``None``。"""
        return await self._events.get()

    def try_next_event(self) -> Optional[Dict[str, Any]]:
        """非阻塞取下一条服务端通知；队列空立即返回 ``None``。"""
        try:
            return self._events.get_nowait()
        except asyncio.QueueEmpty:
            return None

    # ------------------------------------------------------------------
    # 读泵生命周期（子类在 start/shutdown 中调用）
    # ------------------------------------------------------------------

    def _spawn_reader(self) -> None:
        self._reader = asyncio.create_task(self._read_loop())

    async def _stop_reader(self) -> None:
        """停读泵并了结全部在飞请求（幂等保护靠调用方 _closed 标志）。"""
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
            self._reader = None
        self._fail_pending("connection closed")
        await self._events.put(_CLOSE_SENTINEL)

    async def _read_loop(self) -> None:
        """单读泵多路复用：响应帧按 id 回填，通知帧入有序事件队列。"""
        try:
            while True:
                frame = await self._channel.recv_frame()
                if frame is None:
                    break
                if "method" in frame and "id" not in frame:
                    await self._events.put(frame)
                elif "id" in frame:
                    future = self._pending.get(frame["id"])
                    if future is not None and not future.done():
                        future.set_result(frame)
        except asyncio.CancelledError:
            raise
        finally:
            self._fail_pending("connection closed")
            await self._events.put(_CLOSE_SENTINEL)

    def _fail_pending(self, reason: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError(reason))
        self._pending.clear()
