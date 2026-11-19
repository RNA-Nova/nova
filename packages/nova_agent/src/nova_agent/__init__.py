"""nova_agent：事件驱动 Agent、转录循环与工具执行。

跨组件序列化词汇住 ``nova_protocol``，本包门面不中转再导出。
"""

from .agent import Agent
from .agent_loop import (
    DEFAULT_AGENT_IDENTITY,
    run_agent_loop,
    run_agent_loop_continue,
    run_tool_call,
)
from .run import AgentRun
from .stream_fn import get_default_stream_fn, set_default_stream_fn
from .types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEventSink,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentRequestUpdate,
    AgentState,
    AgentStateKwargs,
    AgentTool,
    AgentToolCallOutcome,
    AgentToolUpdateCallback,
    AgentTurnContext,
    AgentTurnDecision,
    BeforeToolCallContext,
    BeforeToolCallResult,
    PrepareNextTurnContext,
    PrepareRequestContext,
    QueueMode,
    RunToolCallOptions,
    StreamFn,
    ToolCallHooks,
    ToolExecutionMode,
)
from .utils import validate_tool_arguments, validate_tool_call

__version__ = "0.1.5"

__all__ = [
    "Agent",
    "AgentRun",
    "DEFAULT_AGENT_IDENTITY",
    "set_default_stream_fn",
    "get_default_stream_fn",
    "run_agent_loop",
    "run_agent_loop_continue",
    "run_tool_call",
    "RunToolCallOptions",
    "AgentContext",
    "AgentState",
    "AgentStateKwargs",
    "AgentLoopConfig",
    "AgentTool",
    "AgentToolCallOutcome",
    "StreamFn",
    "ToolCallHooks",
    "AgentEventSink",
    "AgentToolUpdateCallback",
    "ToolExecutionMode",
    "QueueMode",
    "BeforeToolCallContext",
    "BeforeToolCallResult",
    "AfterToolCallContext",
    "AfterToolCallResult",
    "AgentTurnContext",
    "AgentTurnDecision",
    "PrepareNextTurnContext",
    "PrepareRequestContext",
    "AgentLoopTurnUpdate",
    "AgentRequestUpdate",
    "validate_tool_call",
    "validate_tool_arguments",
]
