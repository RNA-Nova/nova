"""
Nova Agent - 智能代理框架
提供状态管理、事件订阅、消息队列和生命周期控制的Agent类
"""

from .agent import Agent
from .events import (
    # 事件类型
    AgentEvent,
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
    
    # 核心类型
    AgentMessage,
    AgentContext,
    AgentState,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    CustomAgentMessage,
    
    # 类型别名
    ThinkingLevel,
    StreamFn,
    AgentToolUpdateCallback,
)

from .agent_loop import agent_loop, agent_loop_continue, AgentEventStream
from .utils import validate_tool_call, validate_tool_arguments, set_validation_enabled, clear_validator_cache
from .signal import AbortSignal

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
    
    # 类型别名
    "ThinkingLevel",
    "StreamFn",
    "AgentToolUpdateCallback",
    
    # 工具函数
    "validate_tool_call",
    "validate_tool_arguments",
    "set_validation_enabled",
    "clear_validator_cache",

    # 信号函数
    "AbortSignal"
]