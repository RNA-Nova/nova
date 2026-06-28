"""
工具定义类型与运行时包装。

统一描述扩展工具与包管理工具，并提供把 ``ToolDefinition``
包装为底层 ``Agent`` 可调用的 ``DynamicTool``。
"""

import inspect
from typing import Any, Callable, Dict, List, Optional

from nova_agent import AbortSignal, AgentTool, AgentToolResult, ToolExecutionMode
from nova_ai import TextContent
from nova_ai.types.base_model import NovaBaseModel
from pydantic import ConfigDict, Field


class ToolDefinition(NovaBaseModel):
    """Unified tool definition used by both extension tools and package-managed tools.

    执行体通过以下两种方式之一提供：
    - ``execute``: 直接的可调用对象（扩展工具或由 loader 绑定的包管理工具）。
    - ``executor_path``: 包管理工具所在的 ``executor.py`` 路径，由 ``ToolLoader`` 加载后填充 ``execute``。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True)

    name: str
    description: str
    parameters: dict = Field(default_factory=dict)

    label: Optional[str] = None
    execution_mode: Optional[ToolExecutionMode] = None

    # 可选的渲染回调
    render_call: Optional[Callable[[Any], Optional[str]]] = None
    render_result: Optional[Callable[[Any], Optional[str]]] = None

    # 系统提示词元数据
    prompt_snippet: Optional[str] = None
    prompt_guidelines: Optional[List[str]] = None

    # 执行体（二选一）
    execute: Optional[Callable[..., Any]] = None
    executor_path: Optional[str] = None
    tool_dir: Optional[str] = None


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


__all__ = ["DynamicTool", "ToolDefinition"]
