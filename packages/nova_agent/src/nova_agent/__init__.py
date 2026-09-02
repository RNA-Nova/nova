"""
Nova Agent - 智能代理框架
提供状态管理、事件订阅、消息队列和生命周期控制的Agent类
"""

from nova_ai import AbortController, AbortSignal

from .agent import Agent
from .agent_loop import (
    AgentEventStream,
    agent_loop,
    agent_loop_continue,
    run_agent_loop,
    run_agent_loop_continue,
)
from .types import (  # 事件类型; 核心类型; 类型别名与枚举; 钩子上下文与结果
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEndEvent,
    AgentEvent,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentStartEvent,
    AgentState,
    AgentTool,
    AgentToolCall,
    AgentToolResult,
    AgentToolUpdateCallback,
    BeforeToolCallContext,
    BeforeToolCallResult,
    CustomAgentMessage,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ModelThinkingLevel,
    PrepareNextTurnContext,
    QueueMode,
    ShouldStopAfterTurnContext,
    StreamFn,
    ToolExecutionEndEvent,
    ToolExecutionMode,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from .utils import (
    validate_tool_arguments,
    validate_tool_call,
)

# 版本信息
__version__ = "0.1.0"

# 导出公共接口
__all__ = [
    # 主要类
    "Agent",
    "AgentEventStream",
    # 核心函数
    "agent_loop",
    "agent_loop_continue",
    "run_agent_loop",
    "run_agent_loop_continue",
    # 事件类型
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
    # 核心类型
    "AgentMessage",
    "AgentContext",
    "AgentState",
    "AgentLoopConfig",
    "AgentTool",
    "AgentToolResult",
    "CustomAgentMessage",
    "AgentToolCall",
    # 类型别名
    "ModelThinkingLevel",
    "StreamFn",
    "AgentToolUpdateCallback",
    "ToolExecutionMode",
    "QueueMode",
    # 钩子上下文与结果
    "BeforeToolCallContext",
    "BeforeToolCallResult",
    "AfterToolCallContext",
    "AfterToolCallResult",
    "ShouldStopAfterTurnContext",
    "PrepareNextTurnContext",
    "AgentLoopTurnUpdate",
    # 工具函数
    "validate_tool_call",
    "validate_tool_arguments",
    # 信号函数
    "AbortController",
    "AbortSignal",
]
