"""统一通知分发层（对位 Rust client.rs 的 Inner 注册表分发）

连接级通知入口单一化：底层传输收到的每条通知都经 `NotificationRouter.dispatch`
进入，按方法分流、按 handle_id 路由到注册的消费者——一个通知只叫醒该醒的
消费者（而不是每个消费者自挂 on_notification 自过滤全量流量）。

当前承载 fs/readStream 的服务端推送（chunk/done）。进程族通知
（process/output|exited|closed）SDK 侧为轮询消费（process/read），不经推送，
将来接入推送模型时同样注册到本层。

断线语义：连接进入 Failed（恢复失败）时经 `fail_channel` 清扫该通道全部
挂起流（消费者拿到 Failed 事件而非干等）；恢复成功（resumeSessionId 重握手）
时注册表跨重连存活，服务端把推送重定向到新连接，流自然继续。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from .protocol import (
    FS_READ_STREAM_CHUNK,
    FS_READ_STREAM_DONE,
    HTTP_REQUEST_BODY_DELTA,
    FsReadStreamChunkNotification,
    FsReadStreamDoneNotification,
    HttpRequestBodyDeltaNotification,
)

logger = logging.getLogger(__name__)

#: fs/readStream 推送的每流排队上限（块）：默认块 256KB 时约 8MB 缓冲
#: （对位 Rust FS_READ_STREAM_QUEUE_CAPACITY）。慢消费者宁可断流报错，
#: 也不阻塞连接级通知分发、不让内存无界膨胀。
READ_STREAM_QUEUE_CAPACITY = 32


@dataclass(frozen=True)
class ReadStreamEvent:
    """fs/readStream 推送经统一分发转成的流项（对位 Rust FsReadStreamEvent）"""

    kind: Literal["chunk", "done", "failed"]
    #: kind == "chunk" 时的数据块
    chunk: bytes = b""
    #: kind == "done" 时服务端实际推送的总字节数（消费者校验收齐用）
    total_bytes: int | None = None
    #: kind == "failed" 时的错误描述（done 携带 error，或连接失败时客户端合成）
    error: str | None = None


@dataclass
class _StreamRegistration:
    """一条已注册的读流：消费队列 + 所属通道（连接失败清扫按通道过滤）"""

    queue: asyncio.Queue[ReadStreamEvent]
    channel: str | None = field(default=None)


class NotificationRouter:
    """连接级通知统一分发器（注册表 + dispatch 入口）

    - `register_stream`：发 fs/readStream 请求**之前**注册（服务端响应后即开始
      推送，注册不能比推送晚到——对位 Rust register_fs_read_stream 的调用纪律）
    - `dispatch`：传输层 on_notification 的唯一入口（异常留痕不传播——
      通知分发不许炸掉接收循环）
    - `fail_channel`：连接恢复失败时清扫该通道全部挂起流
    """

    def __init__(self) -> None:
        self._streams: dict[str, _StreamRegistration] = {}
        #: 按方法名订阅的通用队列（http body delta、networkPolicyDecision 等）
        self._method_queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def register_method(self, method: str) -> asyncio.Queue[dict[str, Any]]:
        """按方法名注册通用通知订阅（返回消费队列；重复注册追加新队列）"""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._method_queues.setdefault(method, []).append(queue)
        return queue

    def unregister_method_queue(self, method: str, queue: asyncio.Queue) -> None:
        queues = self._method_queues.get(method, [])
        if queue in queues:
            queues.remove(queue)
            if not queues:
                self._method_queues.pop(method, None)

    def register_stream(
        self, handle_id: str, *, channel: str | None = None
    ) -> asyncio.Queue[ReadStreamEvent]:
        """注册 fs/readStream 推送路由，返回消费队列（句柄重复即协议违约）"""
        if handle_id in self._streams:
            raise ValueError(f"fs/readStream handle already registered: {handle_id}")
        queue: asyncio.Queue[ReadStreamEvent] = asyncio.Queue(
            maxsize=READ_STREAM_QUEUE_CAPACITY
        )
        self._streams[handle_id] = _StreamRegistration(queue=queue, channel=channel)
        return queue

    def unregister_stream(self, handle_id: str) -> None:
        """注销推送路由（流结束/放弃时调用，幂等）"""
        self._streams.pop(handle_id, None)

    async def dispatch(self, message: dict[str, Any]) -> None:
        """传输层通知入口：按方法分流、按 handle_id 路由（对位 handle_server_notification）"""
        method = message.get("method")
        try:
            if method == FS_READ_STREAM_CHUNK:
                params = FsReadStreamChunkNotification.model_validate(
                    message.get("params") or {}
                )
                self._deliver_chunk(params)
            elif method == FS_READ_STREAM_DONE:
                params = FsReadStreamDoneNotification.model_validate(
                    message.get("params") or {}
                )
                self._finish_stream(params)
            elif method == HTTP_REQUEST_BODY_DELTA:
                params = HttpRequestBodyDeltaNotification.model_validate(
                    message.get("params") or {}
                )
                event = {"method": method, "params": params.model_dump(by_alias=True)}
                for queues in self._method_queues.values():
                    for q in queues:
                        q.put_nowait(event)
            else:
                queues = self._method_queues.get(method or "")
                if queues:
                    event = {"method": method, "params": message.get("params") or {}}
                    for q in queues:
                        q.put_nowait(event)
                else:
                    logger.debug("ignoring unknown executor notification: %s", method)
        except Exception:
            # 通知载荷畸形不许炸掉传输接收循环——留痕后丢弃（对位 Rust
            # handle_server_notification 出错即断开连接的严格姿态，SDK 侧从宽）
            logger.warning(
                "failed to dispatch executor notification %s", method, exc_info=True
            )

    def fail_channel(self, channel: str | None, message: str) -> None:
        """清扫某通道全部挂起读流（连接恢复失败时调用；消费者收 Failed 事件）"""
        drained = [
            (handle_id, registration)
            for handle_id, registration in self._streams.items()
            if registration.channel == channel
        ]
        for handle_id, registration in drained:
            self._streams.pop(handle_id, None)
            self._push_terminal(
                registration, ReadStreamEvent(kind="failed", error=message)
            )

    def _deliver_chunk(self, params: FsReadStreamChunkNotification) -> None:
        registration = self._streams.get(params.handle_id)
        if registration is None:
            # 未知句柄（流已结束/已放弃）——丢弃，不算错误（对位 Rust 同名分支）
            logger.debug(
                "ignoring fs/readStream chunk for unknown handle %s", params.handle_id
            )
            return
        event = ReadStreamEvent(kind="chunk", chunk=params.chunk)
        try:
            registration.queue.put_nowait(event)
        except asyncio.QueueFull:
            # try_send 背压：消费过慢宁可断流也不阻塞连接级通知分发；
            # 断流表现为消费者收到 Failed（对位 Rust 的 channel 关闭 → UnexpectedEof）
            self._streams.pop(params.handle_id, None)
            self._push_terminal(
                registration,
                ReadStreamEvent(
                    kind="failed",
                    error="fs/readStream consumer too slow: queue is full",
                ),
            )

    def _finish_stream(self, params: FsReadStreamDoneNotification) -> None:
        registration = self._streams.pop(params.handle_id, None)
        if registration is None:
            logger.debug(
                "ignoring fs/readStream done for unknown handle %s", params.handle_id
            )
            return
        if params.error is not None:
            event = ReadStreamEvent(kind="failed", error=params.error)
        else:
            event = ReadStreamEvent(kind="done", total_bytes=params.total_bytes)
        self._push_terminal(registration, event)

    @staticmethod
    def _push_terminal(
        registration: _StreamRegistration, event: ReadStreamEvent
    ) -> None:
        """终止事件必须可达：队列满时丢弃最老数据块腾位（断流语义已由
        total_bytes 校验兜底——丢块即收不齐，消费者必报错，不静默截断）"""
        try:
            registration.queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                registration.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                registration.queue.put_nowait(event)
            except asyncio.QueueFull:  # 理论不可达；防御
                pass
