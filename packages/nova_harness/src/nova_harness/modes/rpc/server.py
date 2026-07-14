"""NovaHarness JSON-RPC server over stdio."""

import json
import traceback
from typing import Any, Callable, Dict, Optional

from nova_harness.modes.rpc.errors import JSONRPCError
from nova_harness.modes.rpc.events import EventSerializer
from nova_harness.modes.rpc.methods import RpcMethods
from nova_harness.modes.rpc.output_guard import OutputGuard
from nova_harness.modes.rpc.protocol import JsonRpcProtocol
from nova_harness.modes.rpc.transport import StdioTransport
from nova_harness.modes.rpc.ui import UIRouter
from nova_harness.modes.rpc.ui_context import RpcUIContext


class NovaRpcServer:
    """Expose nova_harness AgentSession via JSON-RPC over stdio."""

    def __init__(self, output_guard: Optional[OutputGuard] = None) -> None:
        self._output_guard = output_guard
        self._transport = StdioTransport(output_guard=output_guard)
        self._protocol = JsonRpcProtocol()
        self._events = EventSerializer()
        self._methods = RpcMethods()
        self._shutdown = False
        self._ui_context = RpcUIContext(send_request=self._send_ui_request)
        self._ui_router = UIRouter(self._ui_context)
        self._methods.ui_context = self._ui_context

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Main loop: read NDJSON requests from stdin and dispatch."""
        await self._transport.open()
        while not self._shutdown:
            try:
                line = await self._transport.readline()
                if line is None:
                    break
                if not line:
                    continue
                await self._handle_message(json.loads(line))
            except json.JSONDecodeError as exc:
                self._send_error(None, -32700, f"Parse error: {exc}")
            except Exception as exc:
                self._send_error(None, -32603, f"Internal error: {exc}")

    def shutdown(self) -> None:
        """Signal the main loop to stop on the next iteration."""
        self._shutdown = True

    # ------------------------------------------------------------------
    # IO helpers
    # ------------------------------------------------------------------
    def _send(self, obj: Dict[str, Any]) -> None:
        self._transport.write(obj)

    def _send_response(self, id_: Any, result: Any) -> None:
        self._send(self._protocol.response(id_, result))

    def _send_error(self, id_: Any, code: int, message: str, data: Any = None) -> None:
        self._send(self._protocol.error(id_, code, message, data))

    def _send_notification(self, method: str, params: Any) -> None:
        self._send(self._protocol.notification(method, params))

    def _send_ui_request(self, payload: Dict[str, Any]) -> None:
        """Send an extension UI request notification to the frontend."""
        self._send_notification(payload["method"], payload["params"])

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------
    async def _handle_message(self, msg: Dict[str, Any]) -> None:
        if "method" not in msg:
            return
        method: str = msg["method"]
        params: Dict[str, Any] = msg.get("params", {})
        id_: Any = msg.get("id")

        # Route UI inbound methods through the dedicated UI router.
        if self._ui_router.is_ui_method(method):
            result = self._ui_router.route(method, params)
            if result is not None and id_ is not None:
                self._send_response(id_, result)
            elif result is None and id_ is not None:
                self._send_error(id_, -32601, f"Method not found: {method}")
            return

        handler: Optional[Callable] = getattr(self._methods, method, None)
        if handler is None or not callable(handler):
            if id_ is not None:
                self._send_error(id_, -32601, f"Method not found: {method}")
            return

        try:
            result = await handler(params)
            if method == "createSession":
                await self.bind_session_events()
            if id_ is not None:
                self._send_response(id_, result)
            if method == "shutdown":
                self.shutdown()
        except JSONRPCError as exc:
            if id_ is not None:
                self._send_error(id_, exc.code, exc.message, exc.data)
        except Exception as exc:
            traceback.print_exc()
            if id_ is not None:
                self._send_error(id_, -32603, str(exc))

    # ------------------------------------------------------------------
    # Event bridge
    # ------------------------------------------------------------------
    def _on_agent_event(self, event: Any) -> None:
        payload = self._events.serialize(event)
        self._send_notification("agent/event", payload)

    def _create_command_context_actions(self, session: Any, runtime: Any) -> Any:
        """构建扩展 command context 可用的会话控制 action（与 TS rpc-mode 对齐）。"""

        class Actions:
            async def wait_for_idle(_self) -> None:
                agent = getattr(session, "agent", None)
                if agent is not None and hasattr(agent, "wait_for_idle"):
                    await agent.wait_for_idle()

            async def new_session(_self, options: Optional[Any] = None) -> Any:
                return await runtime.new_session(options)

            async def fork(
                _self, entry_id: Optional[str] = None, options: Optional[Any] = None
            ) -> Any:
                result = await runtime.fork(entry_id, options)
                return {"cancelled": result.get("cancelled", False)}

            async def navigate_tree(
                _self, target_id: str, options: Optional[Any] = None
            ) -> Any:
                return await session.navigate_tree(target_id, options)

            async def switch_session(
                _self, path: str, options: Optional[Any] = None
            ) -> Any:
                return await runtime.switch_session(path, options)

        return Actions()

    async def _rebind_session(self, session: Any) -> None:
        """Session 替换后重新绑定扩展并订阅事件。"""
        runtime = self._methods.runtime
        await session.bind_extensions(
            {
                "ui_context": self._ui_context,
                "mode": "rpc",
                "command_context_actions": self._create_command_context_actions(
                    session, runtime
                ),
            }
        )
        session.subscribe(self._on_agent_event)

    # ------------------------------------------------------------------
    # Session lifecycle hooks
    # ------------------------------------------------------------------
    async def bind_session_events(self) -> None:
        """Subscribe the event bridge to the current session and register rebind hook."""
        runtime = self._methods.runtime
        if runtime is not None:
            await runtime.session.bind_extensions(
                {
                    "ui_context": self._ui_context,
                    "mode": "rpc",
                    "command_context_actions": self._create_command_context_actions(
                        runtime.session, runtime
                    ),
                }
            )
            runtime.session.subscribe(self._on_agent_event)
            runtime.set_rebind_session(self._rebind_session)
