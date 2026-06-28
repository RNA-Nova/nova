"""
工具相关类型定义。
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Generic, List, Optional, TypeVar, Union

from nova_ai import ImageContent, TextContent, Tool
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
    terminate: Optional[bool] = None
    """
    Hint that the agent should stop after the current tool batch.
    Early termination only happens when every finalized tool result in the batch sets this to true.
    """


AgentToolUpdateCallback = Callable[[AgentToolResult[Any]], None]
"""Callback for streaming tool execution updates."""


class AgentTool(Tool[TParameters], Generic[TParameters, TDetails], ABC):
    """
    Extends Tool with an execute method and a human‑readable label.
    TParameters: schema type (should match Tool's parameter schema)
    TDetails: type of the details returned in AgentToolResult
    """

    label: str = ""
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
        params: Any,
        signal: Optional[Any] = None,
        on_update: Optional[Any] = None,
    ) -> "AgentToolResult[TDetails]":
        """
        Execute the tool with given parameters.
        - tool_call_id: unique identifier for this tool call
        - params: validated parameters matching TParameters
        - signal: optional cancellation signal (can be an asyncio.Event or similar)
        - on_update: optional callback for streaming partial results
        """
        pass


__all__ = [
    "AgentToolResult",
    "AgentToolUpdateCallback",
    "AgentTool",
    "TDetails",
    "TParameters",
]
