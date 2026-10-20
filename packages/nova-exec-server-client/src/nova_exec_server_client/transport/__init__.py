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

from .base import (
    DisconnectHandler,
    NotificationHandler,
    RequestHandler,
    Transport,
    _JsonRpcTransport,
    _signal_kwarg,
)
from .gate import (
    _DATA_PLANE_NOTIFICATION_METHODS,
    MAX_CLIENT_INBOUND_NOTIFICATION_LEN,
    MAX_CLIENT_INBOUND_REQUEST_LEN,
    MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN,
    MAX_JSONRPC_VALUE_NODES,
    NETWORK_POLICY_DENIAL_REASON,
    _gate_inbound_message,
    _inbound_message_limit,
    _is_malformed_message,
    _is_valid_request_id,
    _json_nodes_within_budget,
    _object_pairs_reject_duplicates,
    _reject_json_constant,
)
from .stdio import (
    _CREATE_NO_WINDOW,
    _NON_INHERITABLE_ENV_VARS,
    StdioTransport,
    _kill_windows_process_tree,
)
from .websocket import WebSocketTransport

__all__ = [
    # 接口协议与基类（base.py）
    "Transport",
    "NotificationHandler",
    "DisconnectHandler",
    "RequestHandler",
    "_JsonRpcTransport",
    "_signal_kwarg",
    # 入站闸门与限流常量（gate.py）
    "MAX_CLIENT_INBOUND_REQUEST_LEN",
    "MAX_CLIENT_INBOUND_NOTIFICATION_LEN",
    "MAX_FS_READ_STREAM_CHUNK_NOTIFICATION_LEN",
    "MAX_JSONRPC_VALUE_NODES",
    "NETWORK_POLICY_DENIAL_REASON",
    "_DATA_PLANE_NOTIFICATION_METHODS",
    "_gate_inbound_message",
    "_inbound_message_limit",
    "_is_malformed_message",
    "_is_valid_request_id",
    "_json_nodes_within_budget",
    "_object_pairs_reject_duplicates",
    "_reject_json_constant",
    # stdio 传输与进程树收割（stdio.py）
    "StdioTransport",
    "_NON_INHERITABLE_ENV_VARS",
    "_CREATE_NO_WINDOW",
    "_kill_windows_process_tree",
    # WebSocket 传输（websocket.py）
    "WebSocketTransport",
]
