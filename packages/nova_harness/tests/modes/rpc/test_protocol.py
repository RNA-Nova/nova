"""
JsonRpcProtocol 单元测试。

覆盖 JSON-RPC 2.0 的 response、error、notification 构造函数。
"""

from nova_harness.modes.rpc.protocol import JsonRpcProtocol


class TestJsonRpcProtocol:
    """JsonRpcProtocol 消息构造测试。"""

    def test_response(self):
        """response 应包含 jsonrpc、id 与 result。"""
        msg = JsonRpcProtocol.response(id_="req-1", result={"ok": True})
        assert msg == {
            "jsonrpc": "2.0",
            "id": "req-1",
            "result": {"ok": True},
        }

    def test_error_with_data(self):
        """error 在 data 非 None 时应包含 data 字段。"""
        msg = JsonRpcProtocol.error(
            id_="req-2",
            code=-32602,
            message="Invalid params",
            data={"field": "missing"},
        )
        assert msg == {
            "jsonrpc": "2.0",
            "id": "req-2",
            "error": {
                "code": -32602,
                "message": "Invalid params",
                "data": {"field": "missing"},
            },
        }

    def test_error_without_data(self):
        """error 在 data 为 None 时不应包含 data 字段。"""
        msg = JsonRpcProtocol.error(
            id_="req-3",
            code=-32601,
            message="Method not found",
        )
        assert msg == {
            "jsonrpc": "2.0",
            "id": "req-3",
            "error": {
                "code": -32601,
                "message": "Method not found",
            },
        }

    def test_error_with_none_id(self):
        """id 为 None 时 error 消息仍可正常构造。"""
        msg = JsonRpcProtocol.error(
            id_=None,
            code=-32700,
            message="Parse error",
        )
        assert msg["id"] is None
        assert msg["error"]["code"] == -32700

    def test_notification(self):
        """notification 应包含 jsonrpc、method 与 params，无 id。"""
        msg = JsonRpcProtocol.notification(
            method="agent/event", params={"type": "text"}
        )
        assert msg == {
            "jsonrpc": "2.0",
            "method": "agent/event",
            "params": {"type": "text"},
        }
        assert "id" not in msg
