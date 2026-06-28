"""
Nova Agent 类型定义统一导出。
"""

from .base import (
    AgentEventSink,
    AgentMessage,
    AgentToolCall,
    CustomAgentMessage,
    QueueMode,
    StreamFn,
    ThinkingLevel,
    ToolExecutionMode,
)
from .context import AgentContext, AgentLoopConfig
from .events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
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
from .tool import AgentTool, AgentToolResult, AgentToolUpdateCallback
from .tool_execution import (
    ExecutedToolCallBatch,
    ExecutedToolCallOutcome,
    FinalizedToolCallOutcome,
    PreparedToolCall,
)

# Resolve forward references now that AgentContext is available.
BeforeToolCallContext.model_rebuild()
AfterToolCallContext.model_rebuild()
ShouldStopAfterTurnContext.model_rebuild()
PrepareNextTurnContext.model_rebuild()
AgentLoopTurnUpdate.model_rebuild()

__all__ = [
    # base
    "AgentEventSink",
    "AgentMessage",
    "AgentToolCall",
    "CustomAgentMessage",
    "QueueMode",
    "StreamFn",
    "ThinkingLevel",
    "ToolExecutionMode",
    # context
    "AgentContext",
    "AgentLoopConfig",
    # events
    "AgentEvent",
    "AgentStartEvent",
    "AgentEndEvent",
    "TurnStartEvent",
    "TurnEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "MessageEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "ToolExecutionEndEvent",
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
    "AgentToolResult",
    "AgentToolUpdateCallback",
    # tool execution
    "ExecutedToolCallOutcome",
    "FinalizedToolCallOutcome",
    "ExecutedToolCallBatch",
    "PreparedToolCall",
]
