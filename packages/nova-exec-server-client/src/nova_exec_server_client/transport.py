"""传输层：连接管理与 JSON-RPC 消息收发

- `WebSocketTransport`：ws:// / wss:// 远程连接（websockets asyncio 新 API）
- `StdioTransport`：spawn 子进程，stdin/stdout 承载 NDJSON JSON-RPC
  （本地 `nova-exec-server --listen stdio` 或 SSH 远程同一实现）
- `Transport`：两种传输与连接池（pool.TransportPool）、恢复包装
  （recovery.ManagedTransport）共同遵守的接口协议，上层只面向它编程

两种传输共享 `_JsonRpcTransport` 基类：pending 请求表、响应/通知分发、
入站限流闸门、服务端反向请求的准入与并发裁决、断线回调
（`on_disconnect`——恢复层据此发起重连，对位 Rust 的
RpcClientEvent::Disconnected 驱动 request_recovery）。

边界处理（重写保留项，勿删）：
- stdio 子进程 spawn 时独立进程组（process_group=0，pgid == 子进程 pid），
  关闭逐级收割进程树：关 stdin 等 EOF 宽限 → terminate 进程组 → kill
  进程组（对位 Rust connection.rs 的 terminate_process_tree /
  kill_process_tree——沙箱 helper 等孙进程随组一并收割，不得漏杀；
  Windows 对位 kill_windows_process_tree：taskkill /PID <pid> /T /F
  杀整树，失败落直接子兜底；Windows spawn 经 CREATE_NO_WINDOW 不分配
  控制台窗口，对位 Rust background_command）
- stdio spawn 的合成 env（继承 + 显式叠加双侧）经
  scrub_non_inheritable_env_vars 剔除启动上下文敏感变量（对位 codex
  protocol shell_environment.rs 的 NON_INHERITABLE_ENV_VARS 名单，
  大小写不敏感比较）
- 客户端 outbound 在飞槽位（对位 Rust rpc.rs 的双信号量）：常规调用
  1024 共享槽、满员立即报错（PendingRequestLimitExceeded）；收摊调用
  （send_request 的 for_cleanup=True——process/terminate、fs/close 等
  收摊路径）先抢共享槽、再抢 1 个收摊保留槽，双无即判连接死亡
  （对位 call_for_cleanup 的 close_transport 熔断）；槽位在响应
  落定/取消/异常时归还（对位 SemaphorePermit drop）
- 出站防线：NaN/Infinity 本地拒发（json allow_nan=False——serde_json
  把非有限浮点序列化为 null，py 的 json 默认印裸 NaN，服务端词法层
  拒收会判连接级致命）；超大消息出站不做本地限长（对位基准
  connection.rs 直发——上限执法归服务端单点（版本演进免漂移），
  超限由服务端杀连接、恢复兜底）
- WS 单消息上限 64MiB（对位 tungstenite 默认 max_message_size）；
  客户端不发 keepalive ping、不靠 pong 看门狗（对位 Rust connection.rs
  from_websocket 的 ping_interval None——pong watchdog 只接在 nova
  未镜像的 relay 面）
- 意外断线即收割底层承载（对位 Rust RpcClient::drop / 连接退役即
  transport.terminate()）：stdio 后台组杀旧进程树、WS 后台关 socket——
  恢复层只造新候选，旧承载（含 stdout EOF 但进程存活、stdin 写失败等
  路径下的残留进程）由本层收尾
- stdio 子进程 stderr 由独立任务按 8KiB 分块持续消费（对位 Rust
  client_transport.rs 的 take() 分块读——行长无上限，超长行分块分发，
  泵永不停），防缓冲填满死锁
- stdio 单条 NDJSON 消息 64MB 上限（对齐服务端 MAX_STDIO_JSONRPC_MESSAGE_LEN；
  StreamReader 行限 +2 为 CRLF 前瞻余量——对位 Rust connection.rs 的
  max+1+CRLF 前瞻：恰好上限的消息带 CRLF 终止也收得下，超限由读回后的
  长度检查拒）
- 客户端入站限流三档（PROTOCOL v1.7，对位 Rust
  client_inbound_request_limit.rs，WS/stdio 双侧生效）：反向请求/未知
  通知/畸形消息 8KiB；数据面通知（process/output|exited|closed、
  http/request/bodyDelta）2MiB；fs/readStream/chunk 8MiB；响应/错误
  不受类别限流（仍受传输层单消息上限约束）。超限即断开连接
- 畸形消息零容忍：JSON 解析失败/形状非法即连接级致命（对位 Rust
  rpc.rs MalformedMessage → 断开+恢复），不静默吞
- 服务端反向请求并发裁决（对位 Rust client_recovery.rs spawn_rpc_reader）：
  准入校验（id 须非负整数、在飞去重，违规即断连+恢复）+ 256 在飞槽
  （满员即时回 fail-closed deny）+ create_task 并发执行，接收循环不堵
- 写路径 drain 提供天然背压（对端读慢时发送方挂起而非内存膨胀）
- 连接断开传播：pending 请求一律以 ConnectionError 收尾
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
from abc import abstractmethod
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Protocol

import websockets.asyncio.client
import websockets.exceptions
from nova_protocol import (
    FS_READ_STREAM_CHUNK,
    HTTP_REQUEST_BODY_DELTA,
    NETWORK_POLICY_REQUEST,
    PROCESS_CLOSED,
    PROCESS_EXITED,
    PROCESS_OUTPUT,
    NetworkPolicyDecision,
    NetworkPolicyRequestResponse,
)

from .errors import (
    JSON_RPC_INTERNAL_ERROR,
    JSON_RPC_METHOD_NOT_FOUND,
    AuthError,
    ConnectionError,
    ExecutorError,
    ProtocolError,
    RequestHandlerError,
    TimeoutError,
)

logger = logging.getLogger(__name__)

#: spawn stdio 子进程时须从合成 env（继承 + 显式覆盖双侧）剔除的变量名单
#: （对位 codex protocol shell_environment.rs 的 NON_INHERITABLE_ENV_VARS，
#: 逐字照抄；大小写不敏感比较——对位 eq_ignore_ascii_case）：防止
#: model 可达的子进程继承 Codex 启动上下文
_NON_INHERITABLE_ENV_VARS = frozenset(
    {
        "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
        "NODE_REPL_AUTH_TOKEN",
        "CODEX_GUARDIAN_DECISIONS_API_KEY",
        "OPENAI_FEDERATION_RULE_ID",
        "OPENAI_IDENTITY_TOKEN_FILE",
        "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
    }
)

#: Windows 后台进程不分配控制台窗口的标志（对位 Rust background_command 的
#: CREATE_NO_WINDOW）；非 Windows 平台的 subprocess 无此常量——该值只在
#: nt 分支使用，取 0 占位（等同不传标志）
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: 通知处理器 / 断线回调 / stderr 回调的签名
NotificationHandler = Callable[[dict], Awaitable[None]]
DisconnectHandler = Callable[[str | None], None]
#: 服务端反向请求处理器：收 params，回 result（异常由传输层转内部错误回执）
RequestHandler = Callable[[dict], Awaitable[Any]]

# ---------------------------------------------------------------------------
# 客户端入站限流（PROTOCOL v1.7，对位 Rust client_inbound_request_limit.rs）
# ---------------------------------------------------------------------------

#: 反向请求 / 未知通知 / 畸形消息的单帧上限（超限即断开连接）
MAX_CLIENT_INBOUND_REQUEST_LEN = 8 * 1024
#: 数据面通知（process/output|exited|closed、http/request/bodyDelta）的放宽档
MAX_CLIENT_INBOUND_NOTIFICATION_LEN = 2 * 1024 * 1024
#: nova 扩展数据面 fs/readStream/chunk 的放宽档（协议最大块 4MiB +
#: base64/分帧余量，对位 Rust MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN）
MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN = 8 * 1024 * 1024

#: 数据面通知放宽档的方法名单（对位 Rust 的豁免 matches!）
_DATA_PLANE_NOTIFICATION_METHODS = frozenset(
    {PROCESS_OUTPUT, PROCESS_EXITED, PROCESS_CLOSED, HTTP_REQUEST_BODY_DELTA}
)

#: 反向请求满员等路径的 fail-closed 拒决原因（对位 Rust NETWORK_POLICY_DENIAL_REASON）
NETWORK_POLICY_DENIAL_REASON = "not_allowed"

#: 入站消息 JSON 值节点预算（对位 Rust MAX_JSONRPC_VALUE_NODES，
#: exec-server-protocol rpc.rs）：根 + 每个数组元素/对象值各记一（键不计）。
#: 满档 fs/walk 响应（5 万条目）约需 15 万节点——256K 留足头部，又挡住
#: "紧凑字节膨胀成百万堆值"的放大攻击。Rust 在解析途中计费；py 在解析后
#: 显式栈遍历计费——消息字节已先受传输层单帧上限约束，瞬时内存有界
MAX_JSONRPC_VALUE_NODES = 256 * 1024


def _reject_json_constant(value: str) -> None:
    """json.loads parse_constant 钩子：NaN/Infinity/-Infinity 非合法 JSON
    （serde_json 词法层即拒收；py 的 json 默认放行，对位拒掉）"""
    raise ValueError(f"invalid JSON constant: {value}")


def _object_pairs_reject_duplicates(pairs: list) -> dict:
    """json.loads object_pairs_hook：对位 Rust BoundedValueVisitor::visit_map
    的重复键拒收（任意嵌套层；py 的 json 默认 last-wins 静默吞掉）"""
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON object key `{key}`")
        obj[key] = value
    return obj


def _json_nodes_within_budget(value: Any) -> bool:
    """节点预算检查（显式栈遍历——不递归，深嵌套不触 Python 递归上限）"""
    remaining = MAX_JSONRPC_VALUE_NODES
    stack = [value]
    while stack:
        node = stack.pop()
        remaining -= 1
        if remaining < 0:
            return False
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return True


def _is_valid_request_id(value: Any) -> bool:
    """对位 Rust RequestId（untagged String | Integer(i64)）的线形校验"""
    if isinstance(value, str):
        return True
    if isinstance(value, bool):  # bool 是 int 子类——JSON true/false 不是合法 id
        return False
    return isinstance(value, int) and -(2**63) <= value < 2**63


def _is_malformed_message(message: Any) -> bool:
    """畸形消息判定——对位 Rust JSONRPCMessage 自定义反序列化（
    exec-server-protocol rpc.rs）的失败面，serde 拒收 = 连接级致命：

    - 非 JSON 对象
    - method 非字符串；method+id 组合中 id 非法（含 null——Rust 按
      contains_key 判型，`id: null` 走 Request 反序列化即失败）
    - 无 method 有 result：须带合法 id（Response）
    - 无 method 无 result：须为完整 error 形态（Error），否则拒收
    """
    if not isinstance(message, dict):
        return True
    if "method" in message:
        if not isinstance(message["method"], str):
            return True
        return "id" in message and not _is_valid_request_id(message["id"])
    if "result" in message:
        return not _is_valid_request_id(message.get("id"))
    error = message.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return not (
        _is_valid_request_id(message.get("id"))
        and isinstance(error, dict)
        and isinstance(code, int)
        and not isinstance(code, bool)
        # serde i64 derive 溢出即畸形（与 _is_valid_request_id 同纪律）
        and -(2**63) <= code < 2**63
        and isinstance(error.get("message"), str)
    )


def _inbound_message_limit(message: dict) -> int | None:
    """按消息类别返回入站字节上限（对位 Rust
    client_inbound_message_exceeded_limit 的分档；None = 响应/错误——
    不受类别限流，仍受传输层单消息上限约束）"""
    method = message.get("method")
    if method is None:
        return None
    if message.get("id") is not None:
        return MAX_CLIENT_INBOUND_REQUEST_LEN
    if method == FS_READ_STREAM_CHUNK:
        return MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN
    if method in _DATA_PLANE_NOTIFICATION_METHODS:
        return MAX_CLIENT_INBOUND_NOTIFICATION_LEN
    return MAX_CLIENT_INBOUND_REQUEST_LEN


def _gate_inbound_message(
    payload: bytes | bytearray,
) -> tuple[dict | None, str | None]:
    """入站消息统一闸门（WS/stdio 共用，对位 Rust connection.rs 读循环的
    parse + client_inbound_message_exceeded_limit 组合）。

    返回 `(message, None)` = 放行分发；`(None, reason)` = 连接级致命
    （断开+恢复）。顺序对位 Rust：先类别限流（畸形消息按 8KiB 档
    fail-closed），再畸形判定。
    """
    encoded_len = len(payload)
    parse_error: Exception | None = None
    try:
        message: Any = json.loads(
            payload,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_object_pairs_reject_duplicates,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as e:
        # JSONDecodeError = 语法层；ValueError = 重复键/非法常量钩子拒收；
        # RecursionError = 超深嵌套（serde_json 自带递归上限；py 靠 json
        # 扫描器的递归保护兜底）——同为连接级致命
        message = None
        parse_error = e
    if message is not None and not _json_nodes_within_budget(message):
        return None, (
            "JSON-RPC message from exec-server exceeds the limit of "
            f"{MAX_JSONRPC_VALUE_NODES} JSON values"
        )
    malformed = message is None or _is_malformed_message(message)
    limit = (
        MAX_CLIENT_INBOUND_REQUEST_LEN if malformed else _inbound_message_limit(message)
    )
    if limit is not None and encoded_len > limit:
        return None, (
            "JSON-RPC message from exec-server exceeds maximum length of "
            f"{limit} bytes"
        )
    if message is None:
        return None, (
            f"failed to parse JSON-RPC message from exec-server: {parse_error}"
        )
    if malformed:
        return None, "malformed JSON-RPC message from exec-server"
    return message, None


class Transport(Protocol):
    """传输层接口协议（结构化类型——实现类无需显式继承）。

    `channel` 参数只在连接池（TransportPool）上有路由意义；单传输实现
    接收但忽略它，保持接口统一。

    `for_cleanup` 标记收摊调用（process/terminate、fs/close 等收摊路径）：
    在飞槽位裁决时先抢共享槽、再抢收摊保留槽（对位 Rust rpc.rs
    call_for_cleanup 的保留槽语义）。

    `on_disconnect` 注册意外断线回调（主动 disconnect 不触发）——恢复层
    （recovery.ManagedTransport）据此发起重连；不参与恢复的调用方可忽略。
    """

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
        for_cleanup: bool = False,
    ) -> Any: ...

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> None: ...

    def on_notification(self, handler: NotificationHandler) -> None: ...

    def register_request_handler(self, method: str, handler: RequestHandler) -> None:
        """注册服务端反向请求处理器（可选能力；恢复层经 getattr 鸭式探测）"""
        ...

    def on_disconnect(self, handler: DisconnectHandler) -> None: ...

    @property
    def is_connected(self) -> bool: ...


class _JsonRpcTransport:
    """JSON-RPC 收发公共机械：pending 表、消息分发、反向请求准入、断线传播。

    子类实现 `_send_message`（写线）并启动各自的接收循环；接收循环逐条过
    `_gate_inbound_message` 闸门后分发——反向请求走 `_dispatch_server_request`
    （准入 + 并发执行）、通知走 `_run_notification_handlers`、响应走
    `_resolve_response`；意外结束时调 `_mark_disconnected`。
    """

    #: 服务端反向请求的在飞槽位上限（对位 Rust client.rs 的
    #: rpc_inbound_request_slots = Semaphore::new(MAX_IN_FLIGHT_SERVER_CALLS)）：
    #: 满员即时 fail-closed 拒决，裁决排队不得堵死接收循环
    MAX_INBOUND_SERVER_CALLS = 256

    #: 常规 outbound 调用的在飞共享槽上限（对位 Rust rpc.rs
    #: MAX_IN_FLIGHT_REGULAR_CALLS）：满员立即报错，不排队
    MAX_IN_FLIGHT_REGULAR_CALLS = 1024
    #: 收摊调用（for_cleanup）的保留槽数（对位 Rust rpc.rs
    #: RESERVED_CLEANUP_CALLS）：共享槽满员时收摊调用仍有最后一格——
    #: 收摊是连接退役前的善后，不得被常规流量饿死
    RESERVED_CLEANUP_CALLS = 1

    def __init__(self, request_timeout: float):
        self.request_timeout = request_timeout
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        #: 在飞 outbound 调用槽位计数（对位 Rust 双信号量的不可用格数——
        #: py 不需要等待语义，整数计数即 try_acquire；响应落定/取消/异常
        #: 即归还，对位 SemaphorePermit drop）
        self._in_flight_regular = 0
        self._in_flight_cleanup = 0
        self._notification_handlers: list[NotificationHandler] = []
        self._disconnect_handlers: list[DisconnectHandler] = []
        self._request_handlers: dict[str, RequestHandler] = {}
        #: 在飞反向请求的 id 集（准入去重；任务收尾即释放——对位 Rust
        #: RpcInboundRequestGuard 的 drop）
        self._inbound_request_ids: set[int] = set()
        #: 在飞反向请求任务（断线/关闭时统一取消——对位 Rust 的
        #: connection_cancelled drop guard：连接退役后裁决不再回包）。
        #: Rust 的槽位信号量跨重连共享（旧连接的在飞请求与新连接并存收尾）；
        #: py 侧断线即取消在飞裁决，重叠窗口不存在，槽位故随连接实例
        self._inbound_tasks: set[asyncio.Task] = set()
        self._closed = True  # 未连接即关闭态

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
        for_cleanup: bool = False,
    ) -> Any:
        """发送 JSON-RPC 请求并等待响应（单传输忽略 channel——连接池路由参数）

        在飞槽位裁决（对位 Rust rpc.rs 的双信号量）：常规调用占
        MAX_IN_FLIGHT_REGULAR_CALLS 共享槽，满员立即报错（对位
        PendingRequestLimitExceeded）；收摊调用（for_cleanup=True——
        process/terminate、fs/close 等收摊路径）先抢共享槽、再抢
        RESERVED_CLEANUP_CALLS 保留槽，双无即判连接死亡（对位
        call_for_cleanup 的 close_transport 熔断 + RpcCallError::Closed）。
        """
        cleanup_reserved = False
        if self._in_flight_regular < self.MAX_IN_FLIGHT_REGULAR_CALLS:
            self._in_flight_regular += 1
        elif for_cleanup and self._in_flight_cleanup < self.RESERVED_CLEANUP_CALLS:
            self._in_flight_cleanup += 1
            cleanup_reserved = True
        elif for_cleanup:
            # 收摊双无：判连接死亡（对位 Rust close_transport 熔断）——
            # 保留槽也耗尽说明连接已堆积上千在飞调用，收摊都排不进
            self._mark_disconnected(
                "exec-server has reached its limit of pending requests "
                "(including the reserved cleanup slot)"
            )
            raise ConnectionError("connection closed")
        else:
            raise ProtocolError(
                "exec-server has reached its limit of "
                f"{self.MAX_IN_FLIGHT_REGULAR_CALLS} pending requests"
            )

        self._request_id += 1
        request_id = self._request_id

        message = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        try:
            await self._send_message(message)
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"request {method} timed out") from None
        except Exception as e:
            # 业务错误（JSON-RPC error 响应的 ProtocolError、连接已断）原样透传，
            # 只把传输层发送失败包装为 ConnectionError
            if isinstance(e, ExecutorError):
                raise
            raise ConnectionError(f"failed to send request {method}: {e}") from e
        finally:
            # 对位 Rust PendingRequestGuard：完成/失败/被取消（CancelledError）
            # 一律即从 pending 表移除——取消残留会等到后续流量或断线才清；
            # 在飞槽位一并归还（对位 SemaphorePermit drop）
            if cleanup_reserved:
                self._in_flight_cleanup -= 1
            else:
                self._in_flight_regular -= 1
            self._pending.pop(request_id, None)

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> None:
        """发送 JSON-RPC 通知（无响应；单传输忽略 channel——连接池路由参数）"""
        await self._send_message(
            {
                "method": method,
                "params": params or {},
            }
        )

    @abstractmethod
    async def _send_message(self, message: dict) -> None:
        """把一条 JSON-RPC 消息写到线上（子类实现各自的线格式）"""

    # ------------------------------------------------------------------
    # 接收分发
    # ------------------------------------------------------------------

    def _resolve_response(self, message: dict) -> None:
        """响应归 pending future：error 响应映射为带结构化 code 的 ProtocolError"""
        request_id = message["id"]
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return
        if "result" in message:
            # result 键优先（对位 serde 反序列化的 contains_key("result") 优先：
            # result+error 并存时 error 键被忽略——与 _is_malformed_message
            # 的归类一致，并存消息按 Response 归）
            future.set_result(message["result"])
        elif "error" in message:
            error = message["error"]
            code = error.get("code")
            future.set_exception(
                ProtocolError(
                    f"JSON-RPC error {code}: {error.get('message')}",
                    code=code,
                    # 线上裸消息原样携带（恢复层全等甄别用——对位 Rust
                    # is_transport_closed_error 比较的是 wire message）
                    server_message=error.get("message"),
                )
            )

    async def _run_notification_handlers(self, message: dict) -> None:
        """通知按序分发给全部处理器（异常逐条 logger.warning 留痕——
        处理器错误不许炸掉接收循环，也不静默吞）"""
        for handler in self._notification_handlers:
            try:
                await handler(message)
            except Exception:
                logger.warning(
                    "notification handler raised on %s",
                    message.get("method"),
                    exc_info=True,
                )

    def on_notification(self, handler: NotificationHandler) -> None:
        """注册通知处理器"""
        self._notification_handlers.append(handler)

    # ------------------------------------------------------------------
    # 服务端反向请求（客户端当服务端：网络沙箱裁决等 method+id 消息）
    # ------------------------------------------------------------------

    def register_request_handler(self, method: str, handler: RequestHandler) -> None:
        """注册服务端发起请求的处理器（按方法名路由；重复注册覆盖旧者）"""
        self._request_handlers[method] = handler

    def unregister_request_handler(self, method: str) -> None:
        """注销服务端请求处理器（幂等）"""
        self._request_handlers.pop(method, None)

    def _dispatch_server_request(self, message: dict) -> None:
        """反向请求的准入与并发分发（对位 Rust client_recovery.rs
        spawn_rpc_reader + rpc.rs admit_inbound_request）。

        接收循环内同步调用、绝不 inline 等用户回调——回调期间后续消息照常
        流动（对位 Rust 的 tokio::spawn 并发执行）。准入纪律：

        - 无处理器：回 METHOD_NOT_FOUND（服务端 fail-closed 拒决），不占槽
          （对位 Rust 分发分支的早退——方法路由先于准入校验）
        - id 须为非负整数（对位 Rust RequestId::Integer >= 0）；字符串/负数/
          布尔 id 即断连+恢复（对位 InvalidRequestId → close_transport +
          request_recovery）
        - id 在飞去重：重复即断连+恢复（对位 DuplicateRequestId）
        - 在飞满槽：即时回 fail-closed 拒决（对位 AtCapacity），不排队
        """
        method = message.get("method") or ""
        message_id = message["id"]
        handler = self._request_handlers.get(method)
        if handler is None:
            self._spawn_inbound_task(
                self._reply_to_server_request(
                    message_id,
                    error={
                        "code": JSON_RPC_METHOD_NOT_FOUND,
                        "message": f"no handler for {method}",
                    },
                )
            )
            return
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id < 0
        ):
            self._mark_disconnected("exec-server sent an invalid request ID")
            return
        if message_id in self._inbound_request_ids:
            self._mark_disconnected("exec-server reused an in-flight request ID")
            return
        if len(self._inbound_request_ids) >= self.MAX_INBOUND_SERVER_CALLS:
            self._spawn_inbound_task(self._reply_at_capacity(message_id, method))
            return
        self._inbound_request_ids.add(message_id)
        self._spawn_inbound_task(self._run_server_request(message, handler))

    def _spawn_inbound_task(self, coro: Coroutine[Any, Any, None]) -> None:
        """反向请求相关协程统一 spawn 并跟踪（断线/关闭时取消）"""
        task = asyncio.create_task(coro)
        self._inbound_tasks.add(task)
        task.add_done_callback(self._inbound_tasks.discard)

    async def _run_server_request(self, message: dict, handler: RequestHandler) -> None:
        """裁决执行任务（对位 Rust tokio::spawn 的决策 future）。

        裁决类请求绝不让服务端干等超时：处理器异常回内部错误（服务端
        fail-closed 拒决）。完成/异常/取消一律释放在飞 id（对位
        RpcInboundRequestGuard drop）；取消（断线收尾）不回包（对位
        connection_cancelled 分支提前返回）。
        """
        message_id = message["id"]
        method = message.get("method") or ""
        try:
            try:
                result = await handler(message.get("params") or {})
            except asyncio.CancelledError:
                raise
            except RequestHandlerError as e:
                # 处理器声明的线上错误（对位 Rust respond_error——如裁决参数
                # 解析失败回 -32602）：按码表原样回包，不兜成内部错误
                await self._reply_to_server_request(
                    message_id, error={"code": e.code, "message": str(e)}
                )
            except Exception as e:
                logger.warning(
                    "server request handler %s failed: %s", method, e, exc_info=True
                )
                await self._reply_to_server_request(
                    message_id,
                    error={
                        "code": JSON_RPC_INTERNAL_ERROR,
                        "message": f"handler failed: {e}",
                    },
                )
            else:
                await self._reply_to_server_request(message_id, result=result)
        finally:
            self._inbound_request_ids.discard(message_id)

    async def _reply_at_capacity(self, message_id: int, method: str) -> None:
        """在飞满槽的即时拒决（对位 Rust AtCapacity 分支）：裁决请求回
        `{deny, not_allowed}`（服务端 fail-closed 拒决）；其他方法回内部
        错误——任何路径都不让服务端干等"""
        if method == NETWORK_POLICY_REQUEST:
            result = NetworkPolicyRequestResponse(
                decision=NetworkPolicyDecision.deny(NETWORK_POLICY_DENIAL_REASON)
            ).model_dump(by_alias=True, exclude_none=True)
            await self._reply_to_server_request(message_id, result=result)
        else:
            await self._reply_to_server_request(
                message_id,
                error={
                    "code": JSON_RPC_INTERNAL_ERROR,
                    "message": (
                        "too many in-flight server requests "
                        f"(limit {self.MAX_INBOUND_SERVER_CALLS})"
                    ),
                },
            )

    async def _reply_to_server_request(
        self,
        message_id: Any,
        *,
        result: Any = None,
        error: dict | None = None,
    ) -> None:
        """回写反向请求响应（写线失败即判连接死亡——对位 Rust respond /
        respond_error 失败后 request_recovery，client_recovery.rs:584-590
        等处：连接已死才让写失败；经断线收尾触发恢复层重建，而非仅留痕
        让服务端干等响应）"""
        reply: dict = {"id": message_id}
        if error is not None:
            reply["error"] = error
        else:
            reply["result"] = result if result is not None else {}
        try:
            await self._send_message(reply)
        except Exception as e:
            self._mark_disconnected(
                f"failed to reply to exec-server request id={message_id}: {e}"
            )

    def _cancel_inbound_tasks(self) -> None:
        """取消在飞反向请求任务（断线/关闭收尾；幂等）"""
        current = asyncio.current_task()
        for task in self._inbound_tasks:
            if task is not current:
                task.cancel()

    async def _drain_inbound_tasks(self) -> None:
        """等已取消的反向请求任务收尾（disconnect 用——事件循环关闭前不留悬挂）"""
        current = asyncio.current_task()
        tasks = [task for task in self._inbound_tasks if task is not current]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # 断线
    # ------------------------------------------------------------------

    def on_disconnect(self, handler: DisconnectHandler) -> None:
        """注册意外断线回调（同步回调；主动 disconnect 不触发）"""
        self._disconnect_handlers.append(handler)

    def _mark_disconnected(self, reason: str | None) -> None:
        """意外断线的统一收尾：pending 以 ConnectionError 收尾、取消在飞反向
        请求、关闭底层承载（`_abort_connection`——对位 Rust 连接退役即
        transport.terminate()）、触发 on_disconnect 回调
        （主动 disconnect 不走这里；多路径并发只生效一次）"""
        if self._closed:
            return
        self._closed = True
        self._fail_pending()
        self._cancel_inbound_tasks()
        self._abort_connection()
        for handler in self._disconnect_handlers:
            try:
                handler(reason)
            except Exception:
                logger.warning("disconnect handler raised", exc_info=True)

    def _abort_connection(self) -> None:
        """意外断线时关闭底层承载（对位 Rust close_transport 的 task abort /
        stdio 的 transport.terminate()）。基类无承载可关；子类覆盖：
        WebSocket 关 socket、stdio 收割进程树——否则恢复路径的旧连接泄漏"""

    def _fail_pending(self) -> None:
        """连接断开收尾：取消所有 pending 请求（幂等）"""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("connection closed"))
        self._pending.clear()


class WebSocketTransport(_JsonRpcTransport):
    """WebSocket 传输层，负责连接管理和 JSON-RPC 消息收发"""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        max_payload: int = 64 * 1024 * 1024,
        request_timeout: float = 30.0,
    ):
        super().__init__(request_timeout)
        self.url = url
        self.token = token
        #: 传输层单消息上限（websockets max_size；默认对位 tungstenite 的
        #: max_message_size=64MiB；类别限流见 `_gate_inbound_message`——
        #: 两道口子语义不同，这里只兜底帧尺寸）
        self.max_payload = max_payload
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._receive_task: asyncio.Task | None = None
        self._abort_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            self._ws = await websockets.asyncio.client.connect(
                self.url,
                additional_headers=headers,
                max_size=self.max_payload,
                # 客户端不发 keepalive ping、不靠 pong 看门狗（对位 Rust
                # connection.rs from_websocket 的 ping_interval None——
                # websockets 库默认 20s 一发，须显式关掉；pong watchdog
                # 只接在 nova 未镜像的 relay 面）
                ping_interval=None,
            )
        except websockets.exceptions.InvalidStatus as e:
            if e.response.status_code == 401:
                raise AuthError(
                    "authentication failed: invalid or missing token"
                ) from e
            raise ConnectionError(f"failed to connect to {self.url}: {e}") from e
        except Exception as e:
            raise ConnectionError(f"failed to connect to {self.url}: {e}") from e

        self._closed = False
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def disconnect(self) -> None:
        """断开连接（主动关闭——不触发 on_disconnect）"""
        self._closed = True
        self._cancel_inbound_tasks()
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._abort_task is not None:
            try:
                await self._abort_task
            except Exception:
                pass
            self._abort_task = None
        await self._drain_inbound_tasks()
        self._fail_pending()

    async def _send_message(self, message: dict) -> None:
        if self._ws is None or self._closed:
            raise ConnectionError("not connected")
        # allow_nan=False：NaN/Infinity 非合法 JSON（serde_json 序列化非有限
        # 浮点为 null；py 的 json 默认印裸 NaN，服务端词法拒收判连接级致命）
        # ——本地抛错不上线
        await self._ws.send(json.dumps(message, allow_nan=False))

    async def _receive_loop(self) -> None:
        """接收消息循环；循环意外结束（对端关闭/读失败/限流/畸形）即断线"""
        reason: str | None = None
        try:
            assert self._ws is not None
            async for raw in self._ws:
                if self._closed:
                    return  # 主动 disconnect：收尾归 disconnect()，不触发回调
                payload = raw if isinstance(raw, (bytes, bytearray)) else raw.encode()
                message, fatal = _gate_inbound_message(payload)
                if fatal is not None:
                    reason = fatal
                    break
                if "method" in message:
                    if message.get("id") is not None:
                        # 服务端反向请求：准入 + 并发执行，接收循环不堵
                        self._dispatch_server_request(message)
                        if self._closed:
                            return  # 准入违规已标记断线（回调已触发）
                    else:
                        await self._run_notification_handlers(message)
                else:
                    self._resolve_response(message)
        except websockets.exceptions.ConnectionClosed as e:
            reason = str(e)
        except Exception as e:  # 读路径其他失败同样传播为断线
            reason = str(e)
            logger.warning("websocket receive loop failed: %s", e)
        finally:
            if not self._closed:
                self._mark_disconnected(reason)

    def _abort_connection(self) -> None:
        """后台关闭 socket，接收循环随即退出（对位 Rust close_transport 的
        transport task abort）"""
        ws = self._ws
        if ws is None or self._abort_task is not None:
            return

        async def close() -> None:
            try:
                await ws.close()
            except Exception:
                logger.debug("closing disconnected websocket failed", exc_info=True)

        self._abort_task = asyncio.create_task(close())

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._closed


def _kill_windows_process_tree(pid: int) -> bool:
    """taskkill 杀整棵进程树（对位 Rust connection.rs 的
    kill_windows_process_tree：/T 整棵子树、/F 强制；stdin/stdout/stderr
    全置 null）。返回 False 时调用方落直接子兜底（对位 kill_direct_child）"""
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as e:
        logger.warning(
            "failed to run taskkill for exec-server stdio process tree %s: %s",
            pid,
            e,
        )
        return False
    return result.returncode == 0


class StdioTransport(_JsonRpcTransport):
    """stdio 传输层：spawn 子进程，stdin/stdout 承载 NDJSON JSON-RPC（一行一条消息）。

    command 参数化对齐 codex 的 `StdioExecServerCommand`（program + args + env + cwd），
    本地与 SSH 远程同一实现——调用方换 command 即可，不为 SSH 写专门类：

    - 本地：`StdioTransport()`（默认 `nova-exec-server --listen stdio`）
    - SSH：`StdioTransport(program="ssh", args=["user@host", "nova-exec-server", "--listen", "stdio"])`

    注意：stderr 由独立任务持续消费（打日志或回调），防止子进程 stderr
    缓冲填满死锁；进程退出传播为连接断开（pending 请求以 ConnectionError 收尾，
    并触发 on_disconnect——恢复层据此重 spawn）。子进程 spawn 时入独立进程组
    （POSIX；Windows 经 CREATE_NO_WINDOW 不分配控制台），关闭/意外断线逐级
    收割整个进程树（POSIX 组杀；Windows taskkill /T /F 杀整树）。
    """

    #: 默认命令：本地 nova-exec-server 的 stdio 监听模式
    DEFAULT_PROGRAM = "nova-exec-server"
    DEFAULT_ARGS = ("--listen", "stdio")
    #: 单条 NDJSON 消息上限，与服务端 MAX_STDIO_JSONRPC_MESSAGE_LEN 对齐
    MAX_MESSAGE_SIZE = 64 * 1024 * 1024
    #: 关闭 stdin 后等待进程自行退出的宽限期（对齐服务端 2s 终止宽限）
    _SHUTDOWN_GRACE = 2.0
    #: stderr 泵的分块读取尺寸（对位 Rust MAX_STDIO_STDERR_LOG_LINE_LEN）
    _STDERR_READ_CHUNK = 8 * 1024

    def __init__(
        self,
        program: str = DEFAULT_PROGRAM,
        args: tuple[str, ...] | list[str] = DEFAULT_ARGS,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        request_timeout: float = 30.0,
        stderr_handler: Callable[[str], None] | None = None,
        max_message_size: int = MAX_MESSAGE_SIZE,
    ):
        super().__init__(request_timeout)
        self.program = program
        self.args = list(args)
        self.env = dict(env) if env else None
        self.cwd = cwd
        self.stderr_handler = stderr_handler
        self.max_message_size = max_message_size
        self._process: asyncio.subprocess.Process | None = None
        self._receive_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._wait_task: asyncio.Task | None = None
        #: 意外断线后的后台进程树收割任务（disconnect 时一并收尾）
        self._reap_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """spawn 子进程并启动收发循环"""
        # 对齐 Rust stdio_command_process：env 叠加在继承环境之上，而非整体
        # 替换；合成 env（继承 + 显式覆盖双侧）再经
        # scrub_non_inheritable_env_vars 剔除启动上下文敏感变量（大小写不敏感，
        # 对位 eq_ignore_ascii_case）
        env = {
            name: value
            for name, value in {**os.environ, **(self.env or {})}.items()
            if name.upper() not in _NON_INHERITABLE_ENV_VARS
        }
        spawn_kwargs: dict[str, Any] = {}
        if os.name == "posix":
            # 独立进程组（pgid == 子进程 pid；对位 Rust client_transport.rs 的
            # process_group(0)）——关闭/断线时 os.killpg 组杀依赖此前提
            spawn_kwargs["process_group"] = 0
        elif os.name == "nt":
            # 后台子进程不分配控制台窗口（对位 Rust client_transport.rs 经
            # background_command 构造的 CREATE_NO_WINDOW）
            spawn_kwargs["creationflags"] = _CREATE_NO_WINDOW
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.program,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
                # StreamReader 行缓冲上限：服务端单条消息可达 64MB；+2 为 CRLF
                # 前瞻余量（对位 Rust connection.rs 的 max+1+CRLF 前瞻——恰好
                # 上限的消息带 CRLF 终止也收得下，超限由读回后的 len 检查拒）
                limit=self.max_message_size + 2,
                **spawn_kwargs,
            )
        except OSError as e:
            self._process = None
            raise ConnectionError(
                f"failed to spawn {self.program} {' '.join(self.args)}: {e}"
            ) from e

        self._closed = False
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())
        self._wait_task = asyncio.create_task(self._wait_loop())

    async def disconnect(self) -> None:
        """断开连接：关 stdin 让服务端自行退出，宽限后 terminate/kill 进程组
        兜底（主动关闭——不触发 on_disconnect）"""
        self._closed = True
        self._cancel_inbound_tasks()
        for task in (self._receive_task, self._stderr_task, self._wait_task):
            if task:
                task.cancel()

        process = self._process
        if process is not None:
            if self._reap_task is not None:
                # 意外断线已起后台收割（同一升级序列、同一进程），等它收尾即可
                try:
                    await self._reap_task
                except Exception:
                    pass
                self._reap_task = None
            else:
                self._close_stdin(process)
                await self._reap_process_tree(process)
            self._process = None

        for task in (self._receive_task, self._stderr_task, self._wait_task):
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._receive_task = None
        self._stderr_task = None
        self._wait_task = None
        await self._drain_inbound_tasks()
        self._fail_pending()

    @staticmethod
    def _close_stdin(process: asyncio.subprocess.Process) -> None:
        """关 stdin：服务端读 EOF 即结束连接并自行退出（优雅路径；已关/已坏幂等）"""
        stdin = process.stdin
        if stdin is not None and not stdin.is_closing():
            try:
                stdin.close()
            except Exception:
                pass

    async def _reap_process_tree(self, process: asyncio.subprocess.Process) -> None:
        """等待进程树退出，逐级升级：EOF 宽限 → terminate 进程组 → kill 进程组
        （对位 Rust terminate_stdio_child + 监督器的 wait → kill_process_tree）。

        直接子已自行退出时仍对组补一发 kill——收割可能存活的孙进程（对位
        Rust 监督器在 child wait 完成后无条件 kill_process_tree）。
        """
        try:
            await asyncio.wait_for(process.wait(), timeout=self._SHUTDOWN_GRACE)
        except asyncio.TimeoutError:
            pass
        else:
            self._kill_process_tree(process)
            return
        self._terminate_process_tree(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=self._SHUTDOWN_GRACE)
        except asyncio.TimeoutError:
            pass
        self._kill_process_tree(process)
        await process.wait()

    @staticmethod
    def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
        """SIGTERM 到整个进程组（对位 Rust terminate_process_tree 的 unix
        分支）；Windows 走 taskkill 杀整树（对位 kill_windows_process_tree），
        组杀/taskkill 失败回退直接子（对位 kill_direct_child 兜底）"""
        if os.name == "posix":
            try:
                # pgid == 直接子 pid（spawn 时 process_group=0）
                os.killpg(process.pid, signal.SIGTERM)
                return
            except ProcessLookupError:
                return  # 组已整体退出
            except OSError as e:
                logger.warning(
                    "failed to terminate exec-server stdio process group %s: %s",
                    process.pid,
                    e,
                )
        elif os.name == "nt":
            if _kill_windows_process_tree(process.pid):
                return
        try:
            process.terminate()
        except ProcessLookupError:
            pass

    @staticmethod
    def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
        """SIGKILL 到整个进程组兜底（对位 Rust kill_process_tree 的 unix
        分支）；Windows 走 taskkill 杀整树，失败回退直接子"""
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
                return
            except ProcessLookupError:
                return
            except OSError as e:
                logger.warning(
                    "failed to kill exec-server stdio process group %s: %s",
                    process.pid,
                    e,
                )
        elif os.name == "nt":
            if _kill_windows_process_tree(process.pid):
                return
        try:
            process.kill()
        except ProcessLookupError:
            pass

    def _abort_connection(self) -> None:
        """收割旧子进程树（对位 Rust 连接退役即 transport.terminate()）——
        恢复层只造新候选不重连旧传输：stdout EOF 但进程存活、stdin 写失败等
        路径下，旧进程及其沙箱 helper 孙进程必须由本层组杀，不得残留"""
        process = self._process
        if process is None or self._reap_task is not None:
            return
        self._reap_task = asyncio.create_task(self._reap_abandoned(process))

    async def _reap_abandoned(self, process: asyncio.subprocess.Process) -> None:
        """后台收割被遗弃的子进程（与 disconnect 同一升级序列；失败仅留痕——
        断线收尾不依赖收割结果）"""
        try:
            self._close_stdin(process)
            await self._reap_process_tree(process)
        except Exception:
            logger.warning(
                "reaping abandoned exec-server process failed", exc_info=True
            )

    async def _send_message(self, message: dict) -> None:
        """写一条 NDJSON 消息到子进程 stdin（drain 提供背压）"""
        process = self._process
        if (
            process is None
            or self._closed
            or process.stdin is None
            or process.stdin.is_closing()
        ):
            raise ConnectionError("not connected")
        line = (
            # allow_nan=False：NaN/Infinity 非合法 JSON（serde_json 序列化非
            # 有限浮点为 null；py 的 json 默认印裸 NaN，服务端词法拒收判连接
            # 级致命）——序列化失败本地抛错不上线
            json.dumps(
                message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        ).encode()
        # 出站不做本地限长（对位基准 connection.rs 直发）——上限执法归
        # 服务端单点，超限由服务端杀连接、恢复兜底
        try:
            process.stdin.write(line)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            self._mark_disconnected(f"stdin write failed: {e}")
            raise ConnectionError(f"failed to write to process stdin: {e}") from e

    async def _receive_loop(self) -> None:
        """读子进程 stdout 的 NDJSON 消息循环"""
        process = self._process
        assert process is not None and process.stdout is not None
        reason: str | None = None
        try:
            while not self._closed:
                line = await process.stdout.readline()
                if not line:
                    reason = "process closed stdout"
                    break  # EOF：进程退出或管道关闭
                if line.endswith(b"\n"):
                    line = line[:-1]
                    if line.endswith(b"\r"):
                        line = line[:-1]
                if len(line) > self.max_message_size:
                    reason = (
                        "JSON-RPC message from exec-server exceeds maximum "
                        f"length of {self.max_message_size} bytes"
                    )
                    break
                if not line.strip():
                    continue
                message, fatal = _gate_inbound_message(line)
                if fatal is not None:
                    reason = fatal
                    break

                if "method" in message:
                    if message.get("id") is not None:
                        # 服务端反向请求（裁决等）：准入 + 并发执行，接收循环不堵
                        self._dispatch_server_request(message)
                        # 准入违规已标记断线时 while 条件即收尾退出
                    else:
                        await self._run_notification_handlers(message)
                else:
                    # 响应同步归 future（接收循环不被处理器阻塞）
                    self._resolve_response(message)
        except (ValueError, OSError) as e:
            # ValueError：单行超过 max_message_size；OSError：管道读取失败
            reason = str(e)
            logger.warning("stdio receive loop failed: %s", e)
        finally:
            if not self._closed:
                self._mark_disconnected(reason)

    async def _stderr_loop(self) -> None:
        """持续消费子进程 stderr，防止缓冲填满死锁（对位 Rust
        client_transport.rs 的 take(8KiB) + read_until 分块读——行长无上限：
        超长行攒满一块即先行分发，泵永不因行长停转）"""
        process = self._process
        assert process is not None and process.stderr is not None
        pending = b""
        try:
            while True:
                chunk = await process.stderr.read(self._STDERR_READ_CHUNK)
                if not chunk:
                    break  # EOF：进程退出或管道关闭
                pending += chunk
                # 完整行逐条分发
                while True:
                    line, sep, rest = pending.partition(b"\n")
                    if not sep:
                        break
                    self._emit_stderr_line(line)
                    pending = rest
                # 超长行：攒满一块即先行分发（对位 take 限长语义）
                while len(pending) >= self._STDERR_READ_CHUNK:
                    self._emit_stderr_line(pending[: self._STDERR_READ_CHUNK])
                    pending = pending[self._STDERR_READ_CHUNK :]
            if pending:
                self._emit_stderr_line(pending)
        except OSError as e:
            logger.warning("failed to read exec-server stdio stderr: %s", e)

    def _emit_stderr_line(self, raw: bytes) -> None:
        """stderr 一行/一块的统一出口：lossy 解码后回调或打日志"""
        text = raw.decode(errors="replace")
        if text.endswith("\r"):
            text = text[:-1]
        if self.stderr_handler is not None:
            try:
                self.stderr_handler(text)
            except Exception:
                logger.warning("stdio stderr handler raised", exc_info=True)
        else:
            logger.debug("nova-exec-server stdio stderr: %s", text)

    async def _wait_loop(self) -> None:
        """进程退出传播为连接断开（receive 循环的 EOF 是主路径，这里兜底）"""
        process = self._process
        assert process is not None
        returncode = await process.wait()
        if not self._closed:
            logger.warning("nova-exec-server stdio process exited: %s", returncode)
            self._mark_disconnected(f"process exited with code {returncode}")

    @property
    def is_connected(self) -> bool:
        return (
            self._process is not None
            and not self._closed
            and self._process.returncode is None
        )
