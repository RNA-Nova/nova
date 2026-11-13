"""Agent 转录上下文与循环配置。"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional, Union

from nova_ai import SimpleStreamOptions
from nova_protocol import AbortSignal, AgentMessage, Message, Model

from .base import ToolExecutionMode
from .hooks import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentLoopTurnUpdate,
    AgentRequestUpdate,
    AgentTurnContext,
    AgentTurnDecision,
    BeforeToolCallContext,
    BeforeToolCallResult,
    PrepareNextTurnContext,
    PrepareRequestContext,
)
from .tool import AgentTool


@dataclass
class AgentContext:
    """循环运行时转录快照；消息由循环原地追加，不使用 Pydantic 容器。"""

    messages: List[AgentMessage] = field(default_factory=list)
    tools: Optional[List[AgentTool]] = None


@dataclass(frozen=True)
class AgentLoopConfig:
    """不可变循环配置；运行中更新一律经 ``dataclasses.replace``。"""

    stream_options: SimpleStreamOptions
    model: Model
    convert_to_llm: Optional[
        Callable[[List[AgentMessage]], Union[List[Message], Awaitable[List[Message]]]]
    ] = None
    """AgentMessage → LLM 消息；自定义消息在此转换或过滤。"""
    transform_context: Optional[
        Callable[
            [List[AgentMessage], Optional[AbortSignal]], Awaitable[List[AgentMessage]]
        ]
    ] = None
    """convert_to_llm 前的可选转录变换（裁剪、外部上下文注入）。"""
    get_api_key: Optional[
        Callable[[str], Union[Optional[str], Awaitable[Optional[str]]]]
    ] = None
    """每次请求动态解析短寿命凭据。"""

    finish_turn: Optional[
        Callable[
            [AgentTurnContext, Optional[AbortSignal]],
            Union[
                AgentTurnDecision,
                Awaitable[Optional[AgentTurnDecision]],
                None,
            ],
        ]
    ] = None
    """工具结果完成后、turn_end 前运行；end/continue 只约束正常回合。"""
    prepare_request: Optional[
        Callable[
            [PrepareRequestContext, Optional[AbortSignal]],
            Union[
                AgentRequestUpdate,
                Awaitable[Optional[AgentRequestUpdate]],
                None,
            ],
        ]
    ] = None
    """每次 provider 请求前运行，包括第一轮；不轮询消息队列。"""
    prepare_next_turn: Optional[
        Callable[
            [PrepareNextTurnContext],
            Union[AgentLoopTurnUpdate, Awaitable[Optional[AgentLoopTurnUpdate]], None],
        ]
    ] = None
    """确定继续后、下一轮 turn_start 前准备替换状态与追加消息。"""
    get_steering_messages: Optional[Callable[[], Awaitable[List[AgentMessage]]]] = None
    """当前助手回合及工具完成后选取插队输入。"""
    get_follow_up_messages: Optional[Callable[[], Awaitable[List[AgentMessage]]]] = None
    """自然收尾后选取后续输入。"""

    tool_execution: ToolExecutionMode = "parallel"
    """parallel 工具共享读门；sequential 工具以写门独占，保留 Nova 并发优势。"""
    before_tool_call: Optional[
        Callable[
            [BeforeToolCallContext, Optional[AbortSignal]],
            Union[
                BeforeToolCallResult,
                Awaitable[Optional[BeforeToolCallResult]],
                None,
            ],
        ]
    ] = None
    """参数校验后、工具执行前；block 可生成错误结果并参与 terminate。"""
    after_tool_call: Optional[
        Callable[
            [AfterToolCallContext, Optional[AbortSignal]],
            Union[
                AfterToolCallResult,
                Awaitable[Optional[AfterToolCallResult]],
                None,
            ],
        ]
    ] = None
    """工具完成后逐字段覆盖结果，先于执行结束事件与转录落账。"""


__all__ = ["AgentContext", "AgentLoopConfig"]
