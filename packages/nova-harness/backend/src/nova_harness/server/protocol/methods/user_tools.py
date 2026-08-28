"""用户工具相关 JSON-RPC 方法。

泛型三件套：``listUserTools`` / ``invokeUserTool`` / ``abortUserTool``
（目录动态、契约恒定）。框架不内置任何用户工具，也不为具体工具提供
别名方法——工具进度经 ``user_tool`` 事件通知透出。
"""

from __future__ import annotations

from nova_harness.server.protocol.errors import JSONRPCError
from nova_harness.server.protocol.methods import shapes
from nova_harness.server.protocol.methods.shapes import (
    AbortResult,
    InvokeUserToolResult,
    ListUserToolsResult,
)
from nova_harness.server.protocol.methods.state import ServerState
from nova_harness.server.protocol.router import MethodRegistry


def register(registry: MethodRegistry, state: ServerState) -> None:
    async def listUserTools(params: shapes.EmptyParams) -> ListUserToolsResult:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return ListUserToolsResult(
            root=[info.dump_wire() for info in state.runtime.session.list_user_tools()]
        )

    async def invokeUserTool(
        params: shapes.InvokeUserToolParams,
    ) -> InvokeUserToolResult:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        try:
            message = await state.runtime.session.invoke_user_tool(
                params.name, params.params or {}
            )
        except KeyError as exc:
            raise JSONRPCError(JSONRPCError.INVALID_PARAMS, str(exc))
        return InvokeUserToolResult(message=message.dump_wire())

    async def abortUserTool(params: shapes.AbortUserToolParams) -> AbortResult:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        state.runtime.session.abort_user_tool(params.name)
        return AbortResult(success=True)

    _D = "user_tools"
    registry.register(
        "listUserTools",
        listUserTools,
        domain=_D,
    )
    registry.register(
        "invokeUserTool",
        invokeUserTool,
        domain=_D,
    )
    registry.register(
        "abortUserTool",
        abortUserTool,
        domain=_D,
    )
