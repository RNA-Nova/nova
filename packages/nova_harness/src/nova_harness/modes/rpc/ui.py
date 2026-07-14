"""RPC 模式下的 UI 原语路由与协议助手。

把所有来自前端的 UI 相关 JSON-RPC 方法（``extension/ui/*``）集中处理，
使 ``server.py`` 只关心通用消息分发，不陷入 UI 细节。

同时提供构造 outbound UI request/notify payload 的辅助函数。
"""

from typing import Any, Dict, Optional

from nova_harness.modes.rpc.ui_context import RpcUIContext


class UIRouter:
    """处理前端发往后的所有 UI 相关 JSON-RPC 消息。"""

    def __init__(self, ui_context: RpcUIContext) -> None:
        self._ui_context = ui_context

    def route(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """根据 method 路由到对应处理函数，返回需要发送的 result（None 表示无需响应）。"""
        handler = self._handlers.get(method)
        if handler is None:
            return None
        return handler(params)

    def is_ui_method(self, method: str) -> bool:
        """判断 method 是否为 UI 相关的前端 inbound 方法。"""
        return method in self._handlers or method.startswith("extension/ui/")

    @property
    def _handlers(self) -> Dict[str, Any]:
        return {
            "extension/ui/response": self._handle_response,
            "extension/ui/capabilities": self._handle_capabilities,
            "extension/ui/register_components": self._handle_register_components,
            "extension/ui/event": self._handle_event,
            "extension/ui/state": self._handle_state,
        }

    def _handle_response(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """前端回复 UI request。"""
        request_id = params.get("id")
        result = params.get("result")
        self._ui_context.resolve_response(request_id, result)
        return {"ok": True}

    def _handle_capabilities(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """前端上报其实现的 UI 原语能力集。"""
        caps = params.get("capabilities", [])
        if isinstance(caps, list):
            self._ui_context.update_capabilities(set(caps))
        return {"ok": True}

    def _handle_register_components(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """前端注册可用自定义组件。"""
        components = params.get("components", [])
        if isinstance(components, list):
            self._ui_context.update_capabilities(
                self._ui_context.capabilities | set(components)
            )
        return {"ok": True}

    def _handle_event(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """前端推送反向 UI 事件（如终端输入）。"""
        event_type = params.get("type")
        data = params.get("data")
        if isinstance(event_type, str):
            self._ui_context.handle_event(event_type, data)
        return {"ok": True}

    def _handle_state(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """前端推送状态快照（编辑器文本、主题、工具展开等）。"""
        state = params.get("state", {})
        if isinstance(state, dict):
            self._ui_context.update_state(state)
        return {"ok": True}


def make_ui_request(
    method: str, request_id: str, params: Dict[str, Any]
) -> Dict[str, Any]:
    """构造标准 UI request 的 JSON-RPC notification payload。"""
    return {"method": f"extension/ui/{method}", "params": {"id": request_id, **params}}


def make_ui_notify(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """构造标准 UI notify 的 JSON-RPC notification payload。"""
    return {"method": f"extension/ui/{method}", "params": params}


def make_capabilities_request() -> Dict[str, Any]:
    """构造请求前端上报能力集的 notification。"""
    return {"method": "extension/ui/capabilities", "params": {}}
