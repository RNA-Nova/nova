"""
工具相关类型定义（组件内部：工具行为契约）。

``AgentToolResult`` 等跨组件词汇已迁 ``nova_protocol``（批次 ②）；
本模块只剩 ``AgentTool`` 行为契约（ABC）与回调签名别名。
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Generic, List, Optional, TypeVar

from nova_protocol import AbortSignal, AgentToolResult, Tool

from .base import ToolExecutionMode

TDetails = TypeVar("TDetails")
"""Type variable for tool execution details."""

TParameters = TypeVar("TParameters")
"""Type variable for tool parameters."""


AgentToolUpdateCallback = Callable[[AgentToolResult[Any]], None]
"""Callback for streaming tool execution updates."""


class AgentTool(Tool, Generic[TParameters, TDetails], ABC):
    """
    Extends Tool with an execute method and a human‑readable label.
    TParameters: 参数类型（与工具包侧 params_model 对应；线上载体
    ``Tool.parameters`` 恒为 JSON Schema dict）
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
    Per-tool execution mode override (gate semantics).
    - "sequential": takes the gate's write lock — runs alone, after all
      previously admitted readers drain.
    - "parallel": shares the read gate with other parallel calls.
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
    "AgentTool",
    "AgentToolUpdateCallback",
    "TDetails",
    "TParameters",
]
