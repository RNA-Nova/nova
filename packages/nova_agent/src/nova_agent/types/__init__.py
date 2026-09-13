"""
Nova Agent 类型定义统一导出（组件内部类型）。

跨组件序列化词汇（AgentMessage / AgentEvent 家族 / AgentToolResult /
CustomAgentMessage）已迁 ``nova_protocol``——消费方一律
``from nova_protocol import X``，本包不中转再导出。
"""

from .base import AgentEventSink, AgentToolCall, QueueMode, StreamFn, ToolExecutionMode
from .context import AgentContext, AgentLoopConfig
from .hooks import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentLoopTurnUpdate,
    BeforeToolCallContext,
    BeforeToolCallResult,
    PrepareNextTurnContext,
    ShouldStopAfterTurnContext,
)
from .state import AgentState
from .tool import AgentTool, AgentToolUpdateCallback
from .tool_execution import (
    ExecutedToolCallBatch,
    ExecutedToolCallOutcome,
    FinalizedToolCallOutcome,
    ImmediateToolCallOutcome,
    PreparedToolCall,
    PreparedToolCallModel,
)

__all__ = [
    # base
    "AgentEventSink",
    "AgentToolCall",
    "QueueMode",
    "StreamFn",
    "ToolExecutionMode",
    # context
    "AgentContext",
    "AgentLoopConfig",
    # hooks
    "BeforeToolCallContext",
    "BeforeToolCallResult",
    "AfterToolCallContext",
    "AfterToolCallResult",
    "ShouldStopAfterTurnContext",
    "PrepareNextTurnContext",
    "AgentLoopTurnUpdate",
    # state
    "AgentState",
    # tool
    "AgentTool",
    "AgentToolUpdateCallback",
    # tool execution
    "ExecutedToolCallOutcome",
    "FinalizedToolCallOutcome",
    "ExecutedToolCallBatch",
    "ImmediateToolCallOutcome",
    "PreparedToolCall",
    "PreparedToolCallModel",
]
