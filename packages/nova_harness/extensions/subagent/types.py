"""Subagent 扩展类型定义。"""

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

SubagentMode = Literal["single", "parallel", "chain"]


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
    usage: SubagentUsage = field(default_factory=SubagentUsage)
    model: Optional[str] = None
    stop_reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
