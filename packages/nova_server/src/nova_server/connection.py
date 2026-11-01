"""连接一等公民（RPC 连接化重构）。

每个接入的客户端（stdio 父进程 / WebSocket / 内存测试端）都是一条
``Connection``：身份、状态机（uninitialized→initialized）、UI 能力集、
在飞请求表、有界出站队列与独立写泵全部挂在连接上——服务器只持有
``ConnectionRegistry``，不再有"那个唯一前端"的隐含假设。

设计对位 codex app-server（asyncio 译本）：

- 连接状态机：``initialized`` 旗标，``initialize`` 方法成功后才上线
  （事件广播门 + UI 寻址都以此为准）；
- 每连接有界出站队列 + 独立写泵：慢写只堵本连接，不队头阻塞别人
  （取代全局写锁）；
- 背压按来源分流（codex origin 语义）：可信来源（stdio/memory）队列满
  则让出式等位；网络来源（websocket）满则判定慢消费者主动断连；
- ``current_connection`` contextvar：handler 任务链（含其派生的
  工具/会话子任务）都能取到本请求的来源连接——UI 寻址"发起方优先"
  与 cancelRequest 的连接隔离都建立在它上面。
"""

from __future__ import annotations

import asyncio
import contextvars
import enum
import itertools
import logging
from typing import Any, Callable, Dict, Optional, Set

from nova_async import TaskTracker
from nova_server.transport.base import Transport

logger = logging.getLogger(__name__)

# 进程级连接 id 发号器（codex ConnectionId AtomicU64 对位）
_connection_ids = itertools.count(1)

# 当前请求的来源连接（dispatcher 在 handler task 入口设置；task 创建即继承，
# 工具执行/会话回调等子任务链全程可见）。无归属调用（agent 自发）为 None。
_current_connection: contextvars.ContextVar[Optional["Connection"]] = (
    contextvars.ContextVar("nova_rpc_connection", default=None)
)


def current_connection() -> Optional["Connection"]:
    """取当前请求上下文的来源连接（无归属调用返回 None）。"""
    return _current_connection.get()


class ConnectionOrigin(str, enum.Enum):
    """连接来源（背压/关停策略按此分流）。"""

    STDIO = "stdio"
    WEBSOCKET = "websocket"
    MEMORY = "memory"


# 可信来源集合：队列满时阻塞等位（父进程/测试端可信，不断连）；
# 网络来源（WEBSOCKET 不在此列）满则慢消费者断连。
_TRUSTED_ORIGINS = {ConnectionOrigin.STDIO, ConnectionOrigin.MEMORY}

_CLOSE = object()  # 写泵停止哨兵


class Connection:
    """一条 RPC 连接：状态 + 出站队列 + 写泵。

    读泵归服务器（``RpcServer._read_pump``）——连接自身只持有写侧。
    """

    def __init__(
        self,
        transport: Transport,
        origin: ConnectionOrigin,
        *,
        queue_size: int = 1024,
        max_inflight: int = 256,
        exit_on_close: bool = False,
        on_closed: Optional[Callable[["Connection"], None]] = None,
    ) -> None:
        self.id: int = next(_connection_ids)
        self.transport = transport
        self.origin = origin
        self.exit_on_close = exit_on_close
        self._on_closed = on_closed
        # 入站背压上限：同时在飞的 handler task 数（本地客户端正常用量是
        # 个位数；超限 = 客户端行为异常）——读泵超限对请求回 -32004、
        # 对通知丢弃，不再无界派生 task
        self.max_inflight = max_inflight
        # initialize 握手完成旗标（服务器在 initialize 成功后置位）
        self.initialized: bool = False
        # 本连接宣告的 UI 能力集（system/capabilities 按连接记账）
        self.ui_capabilities: Set[str] = set()
        # 全部 handler task 的台账（完成自摘 + 关闭落闸 + 取消级联）；
        # len(conn.tasks) 兼作读泵的 overload 在飞计数
        self.tasks = TaskTracker(name=f"conn:{self.id}")
        # 在飞请求 id → handler task（cancelRequest 寻址——tasks 的 id 索引子集）
        self.request_tasks: Dict[Any, asyncio.Task] = {}
        self._outbound: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self._writer_task: Optional[asyncio.Task] = None
        # 慢消费者断连的自关任务槽位（专用：不进 self.tasks——级联取消
        # 会取消它自己，transport 收尸被打断）
        self._close_task: Optional[asyncio.Task] = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    # ------------------------------------------------------------------
    # 出站
    # ------------------------------------------------------------------

    def start_writer(self) -> None:
        """启动写泵（每连接一个，慢写只堵自己）。"""
        self._writer_task = asyncio.create_task(self._write_loop())

    async def send(self, frame: Dict[str, Any]) -> None:
        """异步发送（响应/反向原语等 async 调用点）：满则阻塞本调用方。"""
        if self._closed:
            return
        await self._outbound.put(frame)

    def send_from_sync(self, frame: Dict[str, Any]) -> None:
        """同步上下文发送（会话事件 listener 等 sync 调用点）。

        队列满的背压分流：可信来源让出式等位（转 task 阻塞，不冻事件
        循环）；网络来源判定慢消费者，主动断连（客户端经重 sync 恢复）。
        """
        if self._closed:
            return
        try:
            self._outbound.put_nowait(frame)
        except asyncio.QueueFull:
            if self.origin in _TRUSTED_ORIGINS:
                # 让出式等位：台账登记（防 GC 蒸发丢帧；连接关闭时被级联取消）
                self.tasks.spawn(self._outbound.put(frame), name="outbound-spill")
            else:
                self._spawn_close()

    async def _write_loop(self) -> None:
        while True:
            frame = await self._outbound.get()
            if frame is _CLOSE:
                return
            try:
                await self.transport.write(frame)
            except Exception:
                # 写失败 = 连接实质死亡，走统一关闭路径
                break
        await self.close()

    def _spawn_close(self) -> None:
        """慢消费者断连的自关（sync 上下文触发）：专用槽位持有防 GC。"""
        if self._closed:
            return
        if self._close_task is not None and not self._close_task.done():
            return
        self._close_task = asyncio.create_task(self.close())
        self._close_task.add_done_callback(self._on_close_task_done)

    def _on_close_task_done(self, task: "asyncio.Task[None]") -> None:
        if self._close_task is task:
            self._close_task = None
        if not task.cancelled() and task.exception() is not None:
            # 自关失败的异常必须浮出（兼作 never-retrieved 合法消费）
            logger.error(
                "Connection[%s] 自关任务异常终结", self.id, exc_info=task.exception()
            )

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭连接（幂等）：落闸 → 举旗 → 回调服务器记账 → 排水 → 收资源。

        生命周期协议四段式：close() 返回时本连接名下没有任何在途任务
        （不靠 closed 守卫吞帧兜底，靠排水保证）；handler 内触发关闭是
        合法路径（wait 自动排除调用方自身）。
        """
        if self._closed:
            return
        self._closed = True
        # 1. 落闸：收尾竞态期的晚到任务直接丢弃（spawn 返回 None）
        self.tasks.close()
        # 2. 举旗：在飞 handler 全部硬取消——其 CancelledError 路径的
        #    收尾写帧会被 send() 的 closed 守卫吞掉，不会写入半关的通道
        self.tasks.cancel_all()
        # 3. 服务器侧记账（注册表摘表 / cancelRequest 索引清理 / UI 收尾）
        if self._on_closed is not None:
            self._on_closed(self)
        # 4. 排水：等在飞任务收尾真正收完
        await self.tasks.wait()
        # 5. 收资源：写泵自杀守卫——_write_loop 写失败走到这里时
        #    current_task 就是写泵自身，cancel 自己会让 CancelledError
        #    打断后续的 transport 收尸
        if self._writer_task is not None:
            if self._writer_task is not asyncio.current_task():
                self._writer_task.cancel()
            self._writer_task = None
        try:
            await self.transport.close()
        except Exception:
            pass


class ConnectionRegistry:
    """连接注册表（服务器的唯一连接视图）。"""

    def __init__(self) -> None:
        self._connections: Dict[int, Connection] = {}

    def add(self, conn: Connection) -> None:
        self._connections[conn.id] = conn

    def remove(self, conn: Connection) -> None:
        self._connections.pop(conn.id, None)

    def all(self) -> list[Connection]:
        return list(self._connections.values())

    def initialized(self) -> list[Connection]:
        """已完成 initialize 握手的连接（事件广播/UI 寻址的候选集）。"""
        return [c for c in self._connections.values() if c.initialized]


__all__ = [
    "Connection",
    "ConnectionOrigin",
    "ConnectionRegistry",
    "current_connection",
]
