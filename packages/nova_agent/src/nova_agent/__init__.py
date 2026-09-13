"""
Nova Agent - 智能代理框架
提供状态管理、事件订阅、消息队列和生命周期控制的Agent类

跨组件序列化词汇（消息/事件/AgentToolResult 等）一律住 ``nova_protocol``，
本包门面不中转再导出——消费方用 ``from nova_protocol import X``。
"""

from .agent import Agent
from .agent_loop import (
    AgentEventStream,
    agent_loop,
    agent_loop_continue,
    run_agent_loop,
    run_agent_loop_continue,
)
from .stream_fn import get_default_stream_fn, set_default_stream_fn
from .types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentEventSink,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentState,
    AgentTool,
    AgentToolCall,
    AgentToolUpdateCallback,
    BeforeToolCallContext,
    BeforeToolCallResult,
    PrepareNextTurnContext,
    QueueMode,
    ShouldStopAfterTurnContext,
    StreamFn,
    ToolExecutionMode,
)
from .utils import validate_tool_arguments, validate_tool_call

# 版本信息
__version__ = "0.1.0"

# 导出公共接口
__all__ = [
    # 主要类
    "Agent",
    "set_default_stream_fn",
    "get_default_stream_fn",
    "AgentEventStream",
    # 核心函数
    "agent_loop",
    "agent_loop_continue",
    "run_agent_loop",
    "run_agent_loop_continue",
    # 核心类型（组件内部：运行时容器与行为契约）
    "AgentContext",
    "AgentState",
    "AgentLoopConfig",
    "AgentTool",
    "AgentToolCall",
    # 类型别名
    "StreamFn",
    "AgentEventSink",
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
]
