"""``ToolDefinition`` 与 ``AgentTool`` 互转。

- ``DynamicTool``：把 ``ToolDefinition`` 包装为底层 ``Agent`` 可调用的工具
  （对齐 TS ``wrapToolDefinition``）——``ToolExecContext`` 经
  ``context_provider`` 在每次调用时现取，作为 ``execute`` 第 5 参注入；
- ``create_tool_definition_from_agent_tool``：从纯 ``AgentTool`` 合成
  ``ToolDefinition``（对齐 TS ``createToolDefinitionFromAgentTool``），
  让 override 工具在注册表中同样 definition-first。
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional

from nova_agent import AgentTool, AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContextProvider,
    ToolDefinition,
)


class DynamicTool(AgentTool):
    """把 ``ToolDefinition`` 包装成底层 ``Agent`` 可调用的工具。

    无论工具来自 SDK 自定义（``execute`` 已直接绑定）还是包管理目录
    （``ToolLoader`` 加载 ``executor.py`` 后绑定 ``execute``），都走同一
    包装逻辑。``context_provider`` 缺省时工具拿到 ``NULL_TOOL_EXEC_CONTEXT``
    （standalone loader / 测试场景）。
    """

    def __init__(
        self,
        definition: ToolDefinition,
        context_provider: Optional[ToolContextProvider] = None,
    ) -> None:
        super().__init__(
            name=definition.name,
            description=definition.description,
            parameters=definition.parameters,
            label=definition.label or definition.name,
        )
        self._definition = definition
        self._context_provider: ToolContextProvider = context_provider or (
            lambda: NULL_TOOL_EXEC_CONTEXT
        )
        if definition.execution_mode is not None:
            self.execution_mode = definition.execution_mode

    def prepare_arguments(self, args: Any) -> Any:
        """透传 definition 的参数转换（对齐 TS wrapToolDefinition）。

        definition 未提供时回退基类默认（原样返回）。
        """
        if self._definition.prepare_arguments is not None:
            return self._definition.prepare_arguments(args)
        return super().prepare_arguments(args)

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

        result = execute(
            tool_call_id, params, signal, on_update, self._context_provider()
        )
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


def create_tool_definition_from_agent_tool(tool: AgentTool) -> ToolDefinition:
    """从纯 ``AgentTool`` 合成 ``ToolDefinition``（对齐 TS）。

    SDK/调用方以纯 ``AgentTool`` 覆盖时，合成 definition 保持注册表
    definition-first：prompt 元数据可渲染、来源标识为 ``sdk``。
    纯 ``AgentTool`` 的 ``execute`` 是 4 参签名，包装时丢弃 ctx（对齐
    TS ``createToolDefinitionFromAgentTool`` 的适配）。
    """

    async def execute(
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
        ctx: Any = None,
    ) -> AgentToolResult:
        return await tool.execute(tool_call_id, params, signal, on_update)

    return ToolDefinition(
        name=tool.name,
        label=getattr(tool, "label", None) or tool.name,
        description=getattr(tool, "description", "") or "",
        parameters=getattr(tool, "parameters", None),
        prepare_arguments=getattr(tool, "prepare_arguments", None),
        execute=execute,
        execution_mode=getattr(tool, "execution_mode", None),
        source_info=SourceInfo(path=f"<sdk:{tool.name}>", source="sdk"),
    )


__all__ = ["DynamicTool", "create_tool_definition_from_agent_tool"]
