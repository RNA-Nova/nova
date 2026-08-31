"""JSON-RPC 2.0 错误码与异常。"""

from typing import Any, Optional


class JSONRPCError(Exception):
    """JSON-RPC 可序列化错误。"""

    # 标准 JSON-RPC 2.0 错误码
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # Nova 自定义错误码
    NO_ACTIVE_SESSION = -32000
    SESSION_NOT_FOUND = -32001
    MODEL_NOT_FOUND = -32002
    # 目标会话正在被使用（如删除当前活跃会话）
    SESSION_IN_USE = -32003
    # 连接在飞请求超限（入站背压——codex -32001 overloaded 对位；
    # 码位避让：-32001 已被 SESSION_NOT_FOUND 占用）
    OVERLOADED = -32004
    # 请求被 cancelRequest 取消（对齐 LSP RequestCancelled）
    REQUEST_CANCELLED = -32800

    def __init__(
        self,
        code: int,
        message: str,
        data: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
