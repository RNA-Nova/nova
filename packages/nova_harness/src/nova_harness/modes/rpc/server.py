"""NovaHarness JSON-RPC server over stdio."""

import json
import traceback
from typing import Any, Callable, Dict, Optional

from nova_harness.modes.rpc.errors import JSONRPCError
from nova_harness.modes.rpc.events import EventSerializer
from nova_harness.modes.rpc.methods import RpcMethods
from nova_harness.modes.rpc.protocol import JsonRpcProtocol
from nova_harness.modes.rpc.transport import StdioTransport


class NovaRpcServer:
    """Expose nova_harness AgentSession via JSON-RPC over stdio."""

    def __init__(self) -> None:
        self._transport = StdioTransport()
        self._protocol = JsonRpcProtocol()
        self._events = EventSerializer()
        self._methods = RpcMethods()
        self._shutdown = False

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

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------
    async def _handle_message(self, msg: Dict[str, Any]) -> None:
        if "method" not in msg:
            return
        method: str = msg["method"]
        params: Dict[str, Any] = msg.get("params", {})
        id_: Any = msg.get("id")

        handler: Optional[Callable] = getattr(self._methods, method, None)
        if handler is None or not callable(handler):
            if id_ is not None:
                self._send_error(id_, -32601, f"Method not found: {method}")
            return

        try:
            result = await handler(params)
            if method == "createSession":
                self.bind_session_events()
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

    # ------------------------------------------------------------------
    # Session lifecycle hooks
    # ------------------------------------------------------------------
    def bind_session_events(self) -> None:
        """Subscribe the event bridge to the current session."""
        runtime = self._methods.runtime
        if runtime is not None:
            runtime.session.subscribe(self._on_agent_event)
