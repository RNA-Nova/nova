"""
NovaRpcServer 单元测试。

重点覆盖 _handle_message 的调度逻辑：方法未找到、handler 成功、
JSONRPCError、普通异常、通知、shutdown 与 createSession 事件绑定。
"""

import traceback
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_harness.modes.rpc.errors import JSONRPCError
from nova_harness.modes.rpc.server import NovaRpcServer


def _make_fake_runtime():
    """构造 fake AgentSessionRuntime，用于 createSession 测试。"""
    runtime = MagicMock()
    runtime.session = MagicMock()
    runtime.session.session_id = "session-1"
    runtime.session.session_name = "Test Session"
    runtime.session.subscribe = MagicMock()
    return runtime


class TestNovaRpcServerHandleMessage:
    """NovaRpcServer._handle_message 行为测试。"""

    @pytest.fixture
    def server(self):
        """返回一个 transport.write 被 mock 的 server 实例。"""
        srv = NovaRpcServer()
        srv._transport.write = MagicMock()
        return srv

    def _capture_write(self, server):
        """返回 server 通过 transport.write 发送的所有对象列表。"""
        return [call.args[0] for call in server._transport.write.call_args_list]

    async def test_method_not_found(self, server):
        """方法不存在时应返回 -32601 错误。"""
        await server._handle_message({"id": 1, "method": "nonExistent", "params": {}})

        written = self._capture_write(server)
        assert len(written) == 1
        assert written[0] == {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found: nonExistent"},
        }

    async def test_method_not_found_notification(self, server):
        """通知（无 id）方法不存在时不应发送任何响应。"""
        await server._handle_message({"method": "nonExistent", "params": {}})
        server._transport.write.assert_not_called()

    async def test_handler_success(self, server):
        """handler 成功时应发送 result 响应。"""
        server._methods.initialize = AsyncMock(return_value={"version": "0.1.0"})
        await server._handle_message({"id": 2, "method": "initialize", "params": {}})

        server._methods.initialize.assert_awaited_once_with({})
        written = self._capture_write(server)
        assert written[0] == {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"version": "0.1.0"},
        }

    async def test_handler_jsonrpc_error(self, server):
        """handler 抛 JSONRPCError 时应发送对应错误响应。"""
        server._methods.prompt = AsyncMock(
            side_effect=JSONRPCError(-32000, "No active session", {"detail": "x"})
        )
        await server._handle_message(
            {"id": 3, "method": "prompt", "params": {"text": "hi"}}
        )

        written = self._capture_write(server)
        assert written[0] == {
            "jsonrpc": "2.0",
            "id": 3,
            "error": {
                "code": -32000,
                "message": "No active session",
                "data": {"detail": "x"},
            },
        }

    async def test_handler_regular_exception(self, server):
        """handler 抛普通异常时应发送 -32603 错误并打印 traceback。"""
        server._methods.prompt = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(traceback, "print_exc") as mock_print_exc:
            await server._handle_message(
                {"id": 4, "method": "prompt", "params": {"text": "hi"}}
            )

        mock_print_exc.assert_called_once()
        written = self._capture_write(server)
        assert written[0] == {
            "jsonrpc": "2.0",
            "id": 4,
            "error": {"code": -32603, "message": "boom"},
        }

    async def test_notification_no_response(self, server):
        """通知（无 id）成功执行后不应发送响应。"""
        server._methods.initialize = AsyncMock(return_value={"version": "0.1.0"})
        await server._handle_message({"method": "initialize", "params": {}})

        server._methods.initialize.assert_awaited_once_with({})
        server._transport.write.assert_not_called()

    async def test_notification_error_no_response(self, server):
        """通知（无 id）抛出异常时不应发送错误响应。"""
        server._methods.prompt = AsyncMock(
            side_effect=JSONRPCError(-32000, "No active session")
        )
        await server._handle_message({"method": "prompt", "params": {"text": "hi"}})
        server._transport.write.assert_not_called()

    async def test_shutdown_calls_server_shutdown(self, server):
        """shutdown 方法处理完成后应调用 server.shutdown()。"""
        server._methods.shutdown = AsyncMock(return_value={"ok": True})
        await server._handle_message({"id": 5, "method": "shutdown", "params": {}})

        server._methods.shutdown.assert_awaited_once_with({})
        assert server._shutdown is True
        written = self._capture_write(server)
        assert written[0] == {
            "jsonrpc": "2.0",
            "id": 5,
            "result": {"ok": True},
        }

    async def test_create_session_binds_events(self, server):
        """createSession 成功后应调用 bind_session_events 订阅事件。"""
        fake_runtime = _make_fake_runtime()
        with patch(
            "nova_harness.modes.rpc.methods.create_agent_session",
            new=AsyncMock(return_value=fake_runtime),
        ):
            await server._handle_message(
                {"id": 6, "method": "createSession", "params": {"cwd": "/tmp"}}
            )

        fake_runtime.session.subscribe.assert_called_once()
        assert server._methods.runtime is fake_runtime
        written = self._capture_write(server)
        assert written[0] == {
            "jsonrpc": "2.0",
            "id": 6,
            "result": {
                "session_id": "session-1",
                "session_name": "Test Session",
                "resumed": False,
            },
        }

    async def test_missing_method_key_ignored(self, server):
        """消息中没有 method 键时不应发送任何响应。"""
        await server._handle_message({"id": 7, "params": {}})
        server._transport.write.assert_not_called()


class TestNovaRpcServerRunLoop:
    """NovaRpcServer.run 主循环测试。"""

    async def test_run_loop_decodes_and_dispatches(self):
        """run 循环应读取 NDJSON 并分派到 _handle_message。"""
        server = NovaRpcServer()
        server._transport.readline = AsyncMock(
            side_effect=['{"id":1,"method":"initialize","params":{}}', None]
        )
        server._transport.open = AsyncMock()
        server._handle_message = AsyncMock()

        await server.run()

        server._transport.open.assert_awaited_once()
        assert server._handle_message.await_count == 1
        call_args = server._handle_message.await_args.args[0]
        assert call_args["method"] == "initialize"

    async def test_run_loop_parse_error(self):
        """JSON 解析错误时应发送 Parse error。"""
        server = NovaRpcServer()
        server._transport.readline = AsyncMock(side_effect=["not-json", None])
        server._transport.open = AsyncMock()
        server._transport.write = MagicMock()

        await server.run()

        written = [call.args[0] for call in server._transport.write.call_args_list]
        assert len(written) == 1
        assert written[0]["error"]["code"] == -32700
        assert "Parse error" in written[0]["error"]["message"]

    async def test_run_loop_internal_error(self):
        """_handle_message 外抛异常时应发送 Internal error 并结束循环。"""
        server = NovaRpcServer()
        server._transport.readline = AsyncMock(
            side_effect=[RuntimeError("transport broken"), None]
        )
        server._transport.open = AsyncMock()
        server._transport.write = MagicMock()

        await server.run()

        written = [call.args[0] for call in server._transport.write.call_args_list]
        assert len(written) == 1
        assert written[0]["error"]["code"] == -32603
        assert "Internal error" in written[0]["error"]["message"]
