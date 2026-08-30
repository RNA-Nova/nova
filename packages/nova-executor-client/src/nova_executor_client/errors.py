"""nova-executor SDK 异常定义"""

from __future__ import annotations

# =============================================================================
# JSON-RPC 错误码常量（对位 Rust 服务端 rpc.rs 的码表——调用方按 code 分流用）
# =============================================================================

#: 无效请求（含 resumeSessionId 指向未知/过期会话）
JSON_RPC_INVALID_REQUEST = -32600
#: 方法不存在
JSON_RPC_METHOD_NOT_FOUND = -32601
#: 参数无效
JSON_RPC_INVALID_PARAMS = -32602
#: 服务端内部错误
JSON_RPC_INTERNAL_ERROR = -32603
#: 资源不存在（fs 族端点的 NotFound；对位 rpc.rs 的 not_found）
EXECUTOR_NOT_FOUND = -32004
#: 会话仍附着在别的连接上（resume 竞争；恢复视之为可重试）
SESSION_ALREADY_ATTACHED = -32010


class ExecutorError(Exception):
    """SDK 基础异常"""


class TransportError(ExecutorError):
    """连接/传输错误（新名；`ConnectionError` 别名兼容保留——注意其遮蔽
    内建同名异常，新代码请用 TransportError）"""


#: 兼容别名（历史名遮蔽内建 ConnectionError，新代码用 TransportError）
ConnectionError = TransportError


class AuthError(ExecutorError):
    """认证错误"""


class ProcessError(ExecutorError):
    """进程错误"""


class FileSystemError(ExecutorError):
    """文件系统错误"""


class TimeoutError(ExecutorError):
    """超时错误"""


class ProtocolError(ExecutorError):
    """协议错误（JSON-RPC error 响应 / 协议违约）

    `code` 结构化携带线上 error.code（响应无 code 或本地协议违约时为 None），
    调用方可按码分流——码表见本模块顶部常量（对位 Rust rpc.rs）：

    - `JSON_RPC_INVALID_REQUEST`（-32600）：无效请求；resume 未知/过期会话
    - `JSON_RPC_METHOD_NOT_FOUND`（-32601）：方法不存在
    - `JSON_RPC_INVALID_PARAMS`（-32602）：参数无效
    - `JSON_RPC_INTERNAL_ERROR`（-32603）：服务端内部错误
    - `EXECUTOR_NOT_FOUND`（-32004）：资源不存在
    - `SESSION_ALREADY_ATTACHED`（-32010）：会话仍附着在别的连接上
    """

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code
