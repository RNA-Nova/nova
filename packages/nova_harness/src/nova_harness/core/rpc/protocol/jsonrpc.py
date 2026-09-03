"""JSON-RPC 2.0 消息解析与构造。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from nova_harness.core.rpc.protocol.errors import JSONRPCError


class JsonRpcMessage:
    """JSON-RPC 2.0 消息。"""

    def __init__(
        self,
        method: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        id: Any = None,
        result: Any = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.method = method
        self.params = params or {}
        self.id = id
        self.result = result
        self.error = error

    @property
    def is_request(self) -> bool:
        return self.method is not None and self.id is not None

    @property
    def is_notification(self) -> bool:
        return self.method is not None and self.id is None

    @property
    def is_response(self) -> bool:
        return self.id is not None and self.method is None

    def to_dict(self) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"jsonrpc": "2.0"}
        if self.id is not None:
            msg["id"] = self.id
        if self.method is not None:
            msg["method"] = self.method
            msg["params"] = self.params
        if self.error is not None:
            msg["error"] = self.error
        elif self.method is None and "result" not in msg:
            msg["result"] = self.result
        return msg


def parse_message(raw: Dict[str, Any]) -> JsonRpcMessage:
    """从 dict 解析 JSON-RPC 消息。"""
    if not isinstance(raw, dict) or raw.get("jsonrpc") != "2.0":
        raise JSONRPCError(JSONRPCError.INVALID_REQUEST, "Invalid JSON-RPC 2.0 message")
    return JsonRpcMessage(
        method=raw.get("method"),
        params=raw.get("params", {}),
        id=raw.get("id"),
        result=raw.get("result"),
        error=raw.get("error"),
    )


def build_request(
    method: str, params: Optional[Dict[str, Any]] = None, id: Any = None
) -> JsonRpcMessage:
    return JsonRpcMessage(method=method, params=params or {}, id=id)


def build_response(id: Any, result: Any) -> JsonRpcMessage:
    return JsonRpcMessage(id=id, result=result)


def build_notification(
    method: str, params: Optional[Dict[str, Any]] = None
) -> JsonRpcMessage:
    return JsonRpcMessage(method=method, params=params or {})


def build_error(id: Any, error: JSONRPCError) -> JsonRpcMessage:
    payload: Dict[str, Any] = {"code": error.code, "message": error.message}
    if error.data is not None:
        payload["data"] = error.data
    return JsonRpcMessage(id=id, error=payload)
