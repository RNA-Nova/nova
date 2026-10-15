"""统一通知分发层（对位 Rust client.rs 的 Inner 注册表分发）

连接级通知入口单一化：底层传输收到的每条通知都经 `NotificationRouter.dispatch`
进入，按方法分流、按 handle_id 路由到注册的消费者——一个通知只叫醒该醒的
消费者（而不是每个消费者自挂 on_notification 自过滤全量流量）。

当前承载 fs/readStream 的服务端推送（chunk/done）。进程族通知
（process/output|exited|closed）SDK 侧为轮询消费（process/read），不经推送，
将来接入推送模型时同样注册到本层。

断线语义：服务端断连即 `close_all` fs 句柄表（读流任务错误收尾、done 发往
死连接被弃），resume 只对 process 重挂通知通道——恢复成功后 fs 读流也
**不会继续**。故连接一开始恢复（recovering）即经 `fail_channel` 给该通道
全部挂起消费者（fs 读流 / http 流式响应体 / 方法订阅队列）发终态失败
（对位 Rust recover 起手 `fail_all_http_body_streams`、失败时
`fail_all_in_flight_work`）；恢复失败与连接刷新退役同路径清扫。消费者收
终态失败响亮收尾，绝不干等——不存在"流跨重连自然继续"。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from nova_protocol import (
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
    #: kind == "failed" 时标识服务端句柄是否已收摊/消亡：True = 服务端终态
    #: （done 携带 error）或连接清扫（句柄随连接消亡）——消费者不得再补
    #: fs/close；False = 客户端本地断流（背压队列满/畸形推送兜底），服务端
    #: 读句柄仍活着，消费者须按未收摊路径补发 fs/close（对位 Rust
    #: PushReadRegistration::drop 的 active 语义）
    server_settled: bool = False


@dataclass
class _StreamRegistration:
    """一条已注册的读流：消费队列 + 所属通道（连接失败清扫按通道过滤）"""

    queue: asyncio.Queue[ReadStreamEvent]
    channel: str | None = field(default=None)


#: http 流式响应体的每流队列容量（对位 Rust `http_response_body_stream.rs`
#: 的 256 格）；字节预算 16MB——超任一即终态失败（响亮错误进队列），
#: 不静默丢帧（丢帧会把截断 body 当成功返回，曾是 S 级 bug）。
HTTP_BODY_STREAM_QUEUE_CAPACITY = 256
HTTP_BODY_STREAM_BYTE_BUDGET = 16 * 1024 * 1024

#: 单条 bodyDelta 的解码后上限（对位 Rust `MAX_HTTP_BODY_DELTA_BYTES`）与
#: base64 编码串上限（对位 `MAX_ENCODED_HTTP_BODY_DELTA_BYTES`）——双检，
#: 超即协议错误（SDK 从宽为 fail 对应流，不炸接收循环）
MAX_HTTP_BODY_DELTA_BYTES = 1024 * 1024
MAX_ENCODED_HTTP_BODY_DELTA_BYTES = -(-MAX_HTTP_BODY_DELTA_BYTES // 3) * 4


@dataclass
class _HttpByteBudget:
    """连接级 http 在队字节预算（对位 Rust `Inner.http_body_stream_byte_budget`
    单一信号量：全部并发流共享 16MB——超限时只杀抢不到预算的那条流，其余流
    不受影响）。在队字节随取扣账：排空够快任意大 body 都能流完。"""

    in_flight_bytes: int = 0


@dataclass
class _HttpStreamRegistration:
    """一条已注册的 http 流式响应体通道：消费队列 + 连接级共享预算的引用
    （预算记账在路由器级——对位 Rust 连接级"在队字节"信号量：消费即释放）"""

    budget: _HttpByteBudget
    queue: "_HttpBodyQueue" = field(init=False)

    def __post_init__(self) -> None:
        # 队列与预算互相引用（队列出队即扣连接级预算的在队字节），故队列由
        # 注册项自建
        self.queue = _HttpBodyQueue(HTTP_BODY_STREAM_QUEUE_CAPACITY, self.budget)


class _HttpBodyQueue(asyncio.Queue[dict[str, Any]]):
    """按在队字节记账的队列：投递加、消费减（对位 Rust 的 Semaphore 语义）。
    块大小记在事件包封的 `_n` 键（内部包封，不上线）。

    只覆写 `get_nowait`：asyncio.Queue.get() 的收尾就是 `self.get_nowait()`
    （空队等待被唤醒后同路径），两处都覆写会每条事件双重扣账。"""

    def __init__(self, maxsize: int, budget: _HttpByteBudget) -> None:
        super().__init__(maxsize=maxsize)
        self._budget = budget

    def get_nowait(self) -> dict[str, Any]:
        event = super().get_nowait()
        self._budget.in_flight_bytes -= event.get("_n", 0)
        return event

    def release_queued_bytes(self) -> None:
        """路由注销时释放仍压队的在队字节：消费者已走，残留事件的占账必须
        归还连接级预算——否则幽灵字节耗尽预算、误杀无关流（按流各自记账时
        该泄漏随注册项一起被丢弃，连接级共享后必须显式清账）"""
        while True:
            try:
                self.get_nowait()
            except asyncio.QueueEmpty:
                return


def _http_error_event(request_id: str, reason: str) -> dict[str, Any]:
    """http 流式响应体的合成错误终态事件（done+error——消费者先查 error 转
    ProtocolError；seq 占位 1，对位 Rust fail_all_http_body_streams 的合成
    delta）"""
    return {
        "requestId": request_id,
        "seq": 1,
        "deltaBase64": "",
        "done": True,
        "error": reason,
    }


class NotificationRouter:
    """连接级通知统一分发器（注册表 + dispatch 入口）

    - `register_stream`：发 fs/readStream 请求**之前**注册（服务端响应后即开始
      推送，注册不能比推送晚到——对位 Rust register_fs_read_stream 的调用纪律）
    - `register_http_body_stream`：发 `http/request`（stream_response）之前注册，
      按 requestId 精确路由（对位 Rust http_response_body_stream：每请求一条
      独立通道——广播到所有方法队列会让并发双流互串，曾是 S 级 bug）
    - `dispatch`：传输层 on_notification 的唯一入口（异常留痕不传播——
      通知分发不许炸掉接收循环）
    - `fail_channel`：恢复开始/恢复失败/刷新退役时清扫通道在途推送工作
      （fs 读流 + http 流式响应体 + 方法订阅队列）
    """

    def __init__(self) -> None:
        self._streams: dict[str, _StreamRegistration] = {}
        #: 按方法名订阅的通用队列（networkPolicyDecision 等无请求 id 的通知）
        self._method_queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        #: http 流式响应体的按 requestId 键控队列（每请求一条，对位 Rust）
        self._http_streams: dict[str, _HttpStreamRegistration] = {}
        #: http 在队字节的连接级共享预算（跨全部并发 http 流——对位 Rust
        #: `Inner.http_body_stream_byte_budget` 单一信号量）
        self._http_byte_budget = _HttpByteBudget()
        #: 满队列转异步投递的终止事件任务集（持引用防 GC 中途回收任务；
        #: 完成即自弃——对位 Rust 满员 spawn send 的"任务不滞留"语义）
        self._terminal_tasks: set[asyncio.Task] = set()

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

    def register_http_body_stream(
        self, request_id: str
    ) -> asyncio.Queue[dict[str, Any]]:
        """注册 http 流式响应体通道（按 requestId 键控），返回消费队列"""
        if request_id in self._http_streams:
            raise ValueError(f"http body stream already registered: {request_id}")
        registration = _HttpStreamRegistration(self._http_byte_budget)
        self._http_streams[request_id] = registration
        return registration.queue

    def unregister_http_body_stream(self, request_id: str) -> None:
        """注销 http 流式通道（流结束/放弃时调用，幂等）；仍压队的在队字节
        归还连接级预算（消费者已走，残留占账不得漏成幽灵字节）"""
        registration = self._http_streams.pop(request_id, None)
        if registration is not None:
            registration.queue.release_queued_bytes()

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
                raw_params = message.get("params") or {}
                # per-delta 双检之一：编码串长帽（对位 Rust
                # handle_http_body_delta_notification 的 MAX_ENCODED 预检——
                # 先量串长再解码，超大载荷不付解码代价）
                encoded = (
                    raw_params.get("deltaBase64")
                    if isinstance(raw_params, dict)
                    else None
                )
                if (
                    isinstance(encoded, str)
                    and len(encoded) > MAX_ENCODED_HTTP_BODY_DELTA_BYTES
                ):
                    raise ValueError(
                        f"http response body delta exceeds "
                        f"{MAX_HTTP_BODY_DELTA_BYTES} bytes"
                    )
                params = HttpRequestBodyDeltaNotification.model_validate(raw_params)
                # per-delta 双检之二：解码后 1MB 帽（编码帽取等放行的边界载荷
                # 由此兜底，对位 Rust 的 MAX_HTTP_BODY_DELTA_BYTES 复检）
                if len(params.delta.data) > MAX_HTTP_BODY_DELTA_BYTES:
                    raise ValueError(
                        f"http response body delta exceeds "
                        f"{MAX_HTTP_BODY_DELTA_BYTES} bytes"
                    )
                self._deliver_http_body_delta(params)
            else:
                queues = self._method_queues.get(method or "")
                if queues:
                    event = {"method": method, "params": message.get("params") or {}}
                    for q in queues:
                        q.put_nowait(event)
                else:
                    logger.debug("ignoring unknown executor notification: %s", method)
        except Exception:
            # 通知载荷畸形不许炸掉传输接收循环——留痕后按可提取的句柄/请求 id
            # 合成终态 fail 对应流（不再整条静默丢弃——旧行为下路由永不摘除、
            # 消费者干等）；对位 Rust 分发失败即断开连接的严格姿态，SDK 从宽
            logger.warning(
                "failed to dispatch executor notification %s", method, exc_info=True
            )
            if method in (FS_READ_STREAM_CHUNK, FS_READ_STREAM_DONE):
                self._fail_fs_streams_after_malformed(message, method)
            elif method == HTTP_REQUEST_BODY_DELTA:
                self._fail_http_streams_after_malformed(message)

    def _fail_fs_streams_after_malformed(
        self, message: dict[str, Any], method: str
    ) -> None:
        """畸形 fs 推送的兜底终态：能提出 handleId 只 fail 该流，提不出则 fail
        全表（向 Rust 严格姿态靠拢的兜底——Rust 分发失败即断连、全部流终态；
        SDK 从宽为单流/全表终态，但不许静默丢弃让消费者干等）。

        合成的 failed 为本地终态（server_settled=False）——服务端读句柄很可能
        仍活着（只是它的推送我们没解析动），消费者按未收摊路径补发 fs/close。
        """
        reason = f"malformed {method} notification"
        params = message.get("params")
        handle_id = params.get("handleId") if isinstance(params, dict) else None
        if isinstance(handle_id, str):
            registration = self._streams.pop(handle_id, None)
            if registration is not None:
                self._push_terminal(
                    registration, ReadStreamEvent(kind="failed", error=reason)
                )
            return
        drained = list(self._streams.items())
        self._streams.clear()
        for _handle_id, registration in drained:
            self._push_terminal(
                registration, ReadStreamEvent(kind="failed", error=reason)
            )

    def _fail_http_streams_after_malformed(self, message: dict[str, Any]) -> None:
        """畸形/超限 bodyDelta 的兜底终态（含 per-delta 帽触发的协议错误——
        对位 Rust 判连接级致命的同一路径，SDK 从宽为 fail 对应流）：能提出
        requestId 只 fail 该流，提不出则 fail 全表。终态经 error 事件进队列，
        消费者转 ProtocolError，绝不静默丢帧让消费者干等或收截断 body。"""
        reason = "malformed http/request/bodyDelta notification"
        params = message.get("params")
        request_id = params.get("requestId") if isinstance(params, dict) else None
        if isinstance(request_id, str):
            registration = self._http_streams.pop(request_id, None)
            if registration is not None:
                self._push_http_terminal(
                    registration,
                    _http_error_event(request_id, reason),
                )
            return
        for rid, registration in list(self._http_streams.items()):
            self._http_streams.pop(rid, None)
            self._push_http_terminal(registration, _http_error_event(rid, reason))

    def fail_channel(self, channel: str | None, message: str) -> None:
        """清扫通道在途推送工作（恢复开始/恢复失败/刷新退役时调用；消费者收
        终态失败而非干等）。

        对位 Rust `fail_all_in_flight_work`（`fail_all_fs_read_streams` +
        `fail_all_http_body_streams`）：

        - fs 读流：按注册时的通道标签精确清扫（双通道分离时数据面故障不
          殃及控制面挂起流）
        - http 流式响应体：逐流推 done+error 终态（对位 Rust
          `fail_all_http_body_streams` 的合成 delta——seq 占位 1，消费者
          先查 error 不受影响）
        - 方法订阅队列：逐队列推终态事件（`error` 键携带原因；Rust 无对应
          物——policy 审计订阅为 SDK 糖 API，断线响亮收尾优于干等）

        http 流与方法队列注册入口不见池路由、不打通道标签，任一通道清扫时
        一律随清（这些流量本就走控制面连接；保守全清好过漏清干等）。
        """
        drained = [
            (handle_id, registration)
            for handle_id, registration in self._streams.items()
            if registration.channel == channel
        ]
        for handle_id, registration in drained:
            self._streams.pop(handle_id, None)
            # 连接清扫合成的 failed 标记 server_settled=True：句柄随连接消亡
            # （服务端断连即 close_all 句柄表），消费者不得再补 fs/close
            self._push_terminal(
                registration,
                ReadStreamEvent(kind="failed", error=message, server_settled=True),
            )
        for request_id, registration in list(self._http_streams.items()):
            self._http_streams.pop(request_id, None)
            self._push_http_terminal(
                registration,
                _http_error_event(request_id, message),
            )
        for method, queues in list(self._method_queues.items()):
            self._method_queues.pop(method, None)
            event = {"method": method, "params": {}, "error": message}
            for queue in queues:
                self._push_method_terminal(queue, event)

    def _deliver_http_body_delta(
        self, params: "HttpRequestBodyDeltaNotification"
    ) -> None:
        """按 requestId 精确路由 bodyDelta（对位 Rust http_response_body_stream）。

        队列满或连接级在队字节抢不到预算 → 摘除路由 + 响亮终态（合成 error
        事件进队列，消费者经 delta.error 抛 ProtocolError）——绝不静默丢帧。
        预算是**连接级共享**的"在队字节"（跨全部并发 http 流，消费即释放，
        对位 Rust 的单一信号量）：超限只杀抢不到预算的这一条流。
        """
        registration = self._http_streams.get(params.request_id)
        if registration is None:
            # 未知请求 id（流已结束/已放弃）——丢弃，不算错误
            logger.debug(
                "ignoring http body delta for unknown request %s", params.request_id
            )
            return
        budget = registration.budget
        # 在队字节计解码块长 + error 串长（对位 Rust `queued_bytes` 口径）
        queued_bytes = len(params.delta.data) + len(params.error or "")
        over_budget = (
            budget.in_flight_bytes + queued_bytes > HTTP_BODY_STREAM_BYTE_BUDGET
        )
        event = {
            "method": HTTP_REQUEST_BODY_DELTA,
            "params": params.model_dump(by_alias=True),
            "_n": queued_bytes,
        }
        if over_budget:
            # 未入队不占账；只杀抢不到预算的这一条流（其余流不受影响）
            self._http_streams.pop(params.request_id, None)
            self._push_http_terminal(
                registration,
                _http_error_event(
                    params.request_id,
                    f"http body stream exceeded the "
                    f"{HTTP_BODY_STREAM_BYTE_BUDGET}-byte budget",
                ),
            )
            return
        budget.in_flight_bytes += queued_bytes
        try:
            registration.queue.put_nowait(event)
        except asyncio.QueueFull:
            # 未入队即还账，再按 QueueFull 终态路径杀流
            budget.in_flight_bytes -= queued_bytes
            self._http_streams.pop(params.request_id, None)
            self._push_http_terminal(
                registration,
                _http_error_event(
                    params.request_id,
                    "http body stream consumer too slow: queue is full",
                ),
            )

    @staticmethod
    def _push_http_terminal(
        registration: _HttpStreamRegistration, params: dict[str, Any]
    ) -> None:
        """http 终态事件必须可达：队列满时丢弃最老增量腾位（随后跟的 error
        事件会让消费者抛错——不静默截断）"""
        event = {"method": HTTP_REQUEST_BODY_DELTA, "params": params}
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

    @staticmethod
    def _push_method_terminal(
        queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]
    ) -> None:
        """方法订阅队列的终态事件必须可达：满队列丢最老事件腾位（审计事件
        可丢——丢只影响完整性；终态唤醒不能丢，否则消费者干等）"""
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # 理论不可达；防御
                pass

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
            # 断流表现为消费者收到 Failed（对位 Rust 的 channel 关闭 →
            # UnexpectedEof）。本地合成终态（server_settled=False）——服务端
            # 读句柄仍活着，消费者须按未收摊路径补发 fs/close（对位 Rust
            # PushReadRegistration::drop 的 active=true 分支）
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
            # 服务端错误终态（done 携带 error）：服务端已自行收摊
            event = ReadStreamEvent(
                kind="failed", error=params.error, server_settled=True
            )
        else:
            event = ReadStreamEvent(kind="done", total_bytes=params.total_bytes)
        self._push_terminal(registration, event)

    def _push_terminal(
        self, registration: _StreamRegistration, event: ReadStreamEvent
    ) -> None:
        """终止事件必须可达且**不丢数据块**（对位 Rust client.rs
        `finish_fs_read_stream`）：每流恰好一个终止事件（路由已摘除，这是
        该流的唯一残余推送）——满员时转异步 `put` 排队等位，慢消费者收尾
        阶段可完整成全（收齐全部块后收到终止事件，total_bytes 校验通过）。

        消费方已消失时任务挂起无害：asyncio.Queue 无对端关闭概念（Rust 侧
        接收端消失 send 即败返回），挂起任务不耗资源，随事件循环终结连带
        清理。任务记入 `_terminal_tasks` 持引用，防 GC 中途回收。
        """
        try:
            registration.queue.put_nowait(event)
        except asyncio.QueueFull:
            task = asyncio.create_task(registration.queue.put(event))
            self._terminal_tasks.add(task)
            task.add_done_callback(self._discard_terminal_task)

    def _discard_terminal_task(self, task: "asyncio.Task[None]") -> None:
        """终态投递任务完成即自弃；异常取回留痕（不打 "Task exception was
        never retrieved" 循环告警）"""
        self._terminal_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.debug("terminal event delivery task failed", exc_info=exc)
