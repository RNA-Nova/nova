"""用户工具相关 JSON-RPC 方法。

泛型三件套：``listUserTools`` / ``invokeUserTool`` / ``abortUserTool``
（目录动态、契约恒定）。框架不内置任何用户工具，也不为具体工具提供
别名方法——工具进度经 ``user_tool`` 事件通知透出。
"""

from __future__ import annotations

from typing import Any, Dict, List

from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry


def register(registry: MethodRegistry, state: ServerState) -> None:
    async def listUserTools(params: Dict[str, Any]) -> List[Dict[str, Any]]:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return [info.dump_wire() for info in state.runtime.session.list_user_tools()]

    async def invokeUserTool(params: Dict[str, Any]) -> Dict[str, Any]:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        try:
            message = await state.runtime.session.invoke_user_tool(
                params["name"], params.get("params") or {}
            )
        except KeyError as exc:
            raise JSONRPCError(JSONRPCError.INVALID_PARAMS, str(exc))
        return {"message": message.dump_wire()}

    async def abortUserTool(params: Dict[str, Any]) -> Dict[str, Any]:
        if state.runtime is None:
            return {"ok": False, "reason": "no session"}
        state.runtime.session.abort_user_tool(params.get("name"))
        return {"ok": True}

    from nova_harness.core.rpc.protocol.methods import shapes as _sh

    _D = "user_tools"
    registry.register(
        "listUserTools",
        listUserTools,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.ListUserToolsResult,
    )
    registry.register(
        "invokeUserTool",
        invokeUserTool,
        domain=_D,
        params_model=_sh.InvokeUserToolParams,
        result_model=_sh.InvokeUserToolResult,
    )
    registry.register(
        "abortUserTool",
        abortUserTool,
        domain=_D,
        params_model=_sh.AbortUserToolParams,
        result_model=_sh.AbortResult,
    )
