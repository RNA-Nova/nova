"""RPC UI context implementation.

Bridges UIContext primitives to the frontend via per-primitive JSON-RPC methods:

- Request/response primitives: ``extension/ui/{method}`` notifications with an
  internal ``id``; the frontend answers with ``extension/ui/response``.
- Notify primitives: ``extension/ui/{method}`` fire-and-forget notifications.
- Reverse channels: ``extension/ui/event`` (terminal input etc.) and
  ``extension/ui/state`` (editor text, theme, tool expansion).
- Capability discovery: ``extension/ui/capabilities``.
- Component registration: ``extension/ui/register_components``.

A generic fallback ``extension/ui/request`` / ``extension/ui/response`` is still
supported for custom/extension-defined primitives.
"""

import asyncio
import uuid
from typing import Any, Callable, Dict, List, Optional, Set

from nova_harness.core.types.ui import UIContext, UIResponse
from nova_harness.modes.rpc.primitives import TerminalInputHandler


class RpcUIContext(UIContext):
    """UI context that forwards primitives to a JSON-RPC frontend."""

    def __init__(
        self,
        send_request: Callable[[Dict[str, Any]], None],
        default_timeout: float = 300.0,
        capabilities: Optional[Set[str]] = None,
    ) -> None:
        self._send_request = send_request
        self._default_timeout = default_timeout
        self._capabilities: Set[str] = set(capabilities) if capabilities else set()
        self._pending: Dict[str, asyncio.Future] = {}
        self._terminal_input_handlers: List[TerminalInputHandler] = []
        self._state: Dict[str, Any] = {}

    @property
    def capabilities(self) -> Set[str]:
        return set(self._capabilities)

    def update_capabilities(self, capabilities: Set[str]) -> None:
        """Called by the RPC server when frontend capabilities are reported."""
        self._capabilities = set(capabilities)

    def _next_id(self) -> str:
        return uuid.uuid4().hex

    def resolve_response(self, request_id: str, result: Any) -> None:
        """Called by the RPC server when an ``extension/ui/response`` arrives."""
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(result)

    # ------------------------------------------------------------------
    # Core request/notify forwarding
    # ------------------------------------------------------------------

    async def request(self, method: str, params: Dict[str, Any]) -> UIResponse:
        """发送一个需要响应的 UI request。

        标准原语使用 ``extension/ui/{method}``；自定义原语回退到
        ``extension/ui/request``。
        """
        if not self.has_capability(method):
            return UIResponse(cancelled=True)

        request_id = self._next_id()
        if self._is_standard_request_method(method):
            rpc_method = f"extension/ui/{method}"
            rpc_params: Dict[str, Any] = {"id": request_id, **params}
        else:
            rpc_method = "extension/ui/request"
            rpc_params = {"id": request_id, "method": method, "params": params}
        payload = {
            "method": rpc_method,
            "params": rpc_params,
        }
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        try:
            self._send_request(payload)
            result = await asyncio.wait_for(future, timeout=self._default_timeout)
            return self._normalize_response(result)
        except asyncio.TimeoutError:
            return UIResponse(cancelled=True)
        finally:
            self._pending.pop(request_id, None)

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        """发送一个不需要响应的 UI 通知。"""
        if not self.has_capability(method):
            return
        rpc_method = (
            f"extension/ui/{method}"
            if self._is_standard_notify_method(method)
            else "extension/ui/request"
        )
        self._send_request(
            {
                "method": rpc_method,
                "params": params,
            }
        )

    # ------------------------------------------------------------------
    # Reverse channels
    # ------------------------------------------------------------------

    def on_terminal_input(self, handler: TerminalInputHandler) -> Callable[[], None]:
        """注册终端输入监听器。"""
        self._terminal_input_handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._terminal_input_handlers:
                self._terminal_input_handlers.remove(handler)

        return unsubscribe

    def handle_event(self, event_type: str, data: Any) -> None:
        """处理前端通过 ``extension/ui/event`` 上报的事件。"""
        if event_type != "terminalInput":
            return
        for handler in self._terminal_input_handlers:
            result = handler(data)
            if isinstance(result, dict) and result.get("consume"):
                break

    def update_state(self, state: Dict[str, Any]) -> None:
        """处理前端通过 ``extension/ui/state`` 同步的状态。"""
        self._state.update(state)

    # ------------------------------------------------------------------
    # Synchronous getters backed by frontend state sync
    # ------------------------------------------------------------------

    def get_editor_text(self) -> str:
        return self._state.get("editorText", "")

    def get_all_themes(self) -> List[Dict[str, Any]]:
        return self._state.get("allThemes", [])

    def get_theme(self, name: str) -> Optional[Dict[str, Any]]:
        themes = self._state.get("themes", {})
        return themes.get(name)

    def get_tools_expanded(self) -> bool:
        return self._state.get("toolsExpanded", False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize_response(self, result: Any) -> UIResponse:
        """把前端返回的结果归一化为 UIResponse。"""
        if result is None:
            return UIResponse(cancelled=True)
        if isinstance(result, UIResponse):
            return result
        if isinstance(result, dict):
            # 如果 dict 包含标准 UI response 字段，按结构化解析；否则保留原 dict。
            if any(k in result for k in ("value", "cancelled", "confirmed")):
                return UIResponse.model_validate(result)
            return UIResponse(value=result)
        return UIResponse(value=result)

    def _is_standard_request_method(self, method: str) -> bool:
        return method in {
            "select",
            "confirm",
            "input",
            "editor",
            "custom",
            "setTheme",
        }

    def _is_standard_notify_method(self, method: str) -> bool:
        return method in {
            "notify",
            "setStatus",
            "setWorkingMessage",
            "setWorkingVisible",
            "setWorkingIndicator",
            "setHiddenThinkingLabel",
            "setWidget",
            "setFooter",
            "setHeader",
            "setTitle",
            "pasteToEditor",
            "setEditorText",
            "setEditorComponent",
            "setToolsExpanded",
            "addAutocompleteProvider",
        }
