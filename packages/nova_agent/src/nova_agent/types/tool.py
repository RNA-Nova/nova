"""
工具相关类型定义。
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Generic, List, Optional, TypeVar, Union

from nova_ai import AbortSignal, ImageContent, TextContent, Tool
from nova_ai.types.base_model import NovaBaseModel

from .base import ToolExecutionMode

TDetails = TypeVar("TDetails")
"""Type variable for tool execution details."""

TParameters = TypeVar("TParameters")
"""Type variable for tool parameters (schema)."""


class AgentToolResult(NovaBaseModel, Generic[TDetails]):
    """Result of a tool execution."""

    content: List[Union[TextContent, ImageContent]]
    """Content blocks supporting text and images."""
    details: TDetails
    """Details to be displayed in a UI or logged."""
    added_tool_names: Optional[List[str]] = None
    """Names of tools introduced by this result and available from this transcript point onward."""
    terminate: Optional[bool] = None
    """
    Hint that the agent should stop after the current tool batch.
    Early termination only happens when every finalized tool result in the batch sets this to true.
    """
    is_error: bool = False
    """
    结果级错误标记（pi 对齐）：工具对**预期内失败**（非零退出、文件不存在、
    参数非法等）返回结果时置 True——驱动 toolResult.is_error 与 UI 错误卡片。
    异常路径（未捕获异常）由执行框架另行标记，不走此字段。
    """


AgentToolUpdateCallback = Callable[[AgentToolResult[Any]], None]
"""Callback for streaming tool execution updates."""


class AgentTool(Tool[TParameters], Generic[TParameters, TDetails], ABC):
    """
    Extends Tool with an execute method and a human‑readable label.
    TParameters: schema type (should match Tool's parameter schema)
    TDetails: type of the details returned in AgentToolResult
    """

    label: str
    """A human-readable label for the tool to be displayed in UI."""

    def prepare_arguments(self, args: Any) -> Any:
        """
        Optional compatibility shim for raw tool-call arguments before schema validation.
        Must return an object that matches TParameters.
        """
        return args

    execution_mode: Optional[ToolExecutionMode] = None
    """
    Per-tool execution mode override.
    - "sequential": this tool must execute one at a time with other tool calls.
    - "parallel": this tool can execute concurrently with other tool calls.
    If omitted, the config-level tool_execution mode applies.
    """

    @abstractmethod
    async def execute(
        self,
        tool_call_id: str,
        params: TParameters,
        signal: Optional[AbortSignal] = None,
        on_update: Optional[Callable[[AgentToolResult[TDetails]], None]] = None,
    ) -> "AgentToolResult[TDetails]":
        """
        Execute the tool with given parameters.
        - tool_call_id: unique identifier for this tool call
        - params: validated parameters matching TParameters
        - signal: optional cancellation signal
        - on_update: optional callback for streaming partial results
        """
        ...


__all__ = [
    "AgentToolResult",
    "AgentToolUpdateCallback",
    "AgentTool",
    "TDetails",
    "TParameters",
]
