"""Subagent 类型定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

SubagentMode = Literal["single", "parallel", "chain"]
AgentScope = Literal["user", "project", "both"]


@dataclass
class AgentInfo:
    """已发现的 agent 配置摘要。"""

    name: str
    source: str  # "user" | "project"
    model: Optional[str] = None
    tools: list = field(default_factory=list)


@dataclass
class SubagentCall:
    """一次子 agent 调用配置。"""

    agent: str
    task: str
    cwd: Optional[str] = None


@dataclass
class SubagentUsage:
    """子 agent 会话的用量统计。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0
    context_tokens: int = 0
    turns: int = 0


@dataclass
class SubagentResult:
    """单次子 agent 执行结果。"""

    agent: str
    task: str
    output: str = ""
    error: Optional[str] = None
    error_message: Optional[str] = None
    exit_code: int = 0
    usage: SubagentUsage = field(default_factory=SubagentUsage)
    model: Optional[str] = None
    stop_reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
