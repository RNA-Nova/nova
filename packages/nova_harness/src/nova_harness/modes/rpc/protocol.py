"""JSON-RPC 2.0 message builders."""

from typing import Any, Dict, Optional


class JsonRpcProtocol:
    """Construct JSON-RPC 2.0 request/response/notification objects."""

    @staticmethod
    def response(id_: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": id_, "result": result}

    @staticmethod
    def error(
        id_: Optional[Any], code: int, message: str, data: Any = None
    ) -> Dict[str, Any]:
        err: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {"jsonrpc": "2.0", "id": id_, "error": err}

    @staticmethod
    def notification(method: str, params: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "method": method, "params": params}
