"""把 ``ToolDefinition`` 包装为底层 ``Agent`` 可调用的 ``DynamicTool``。"""

from __future__ import annotations

import inspect
from typing import Any, Dict, Optional

from nova_agent import AbortSignal, AgentTool, AgentToolResult
from nova_ai import TextContent

from nova_harness.core.types.runtime.tools import ToolDefinition


class DynamicTool(AgentTool):
    """把 ``ToolDefinition`` 包装成底层 ``Agent`` 可调用的工具。

    无论工具来自扩展（``execute`` 已直接绑定）还是包管理目录
    （``ToolLoader`` 加载 ``executor.py`` 后绑定 ``execute``），都走同一包装逻辑。
    """

    def __init__(self, definition: ToolDefinition) -> None:
        super().__init__(
            name=definition.name,
            description=definition.description,
            parameters=definition.parameters,
        )
        self._definition = definition
        self.label = definition.label or definition.name
        if definition.execution_mode is not None:
            self.execution_mode = definition.execution_mode

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
    ) -> AgentToolResult:
        execute = self._definition.execute
        if execute is None:
            return AgentToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=f"Tool '{self._definition.name}' has no execute handler",
                    )
                ],
                details={"error": "missing execute handler"},
            )

        result = execute(tool_call_id, params, signal, on_update)
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, AgentToolResult):
            return result
        if result is None:
            return AgentToolResult(content=[], details=None)
        if isinstance(result, str):
            return AgentToolResult(
                content=[TextContent(type="text", text=result)], details=None
            )
        return AgentToolResult(
            content=[TextContent(type="text", text=str(result))],
            details=result if not isinstance(result, str) else None,
        )


__all__ = ["DynamicTool"]
