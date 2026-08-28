"""Subagent 类型定义。"""

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
    error_message: Optional[str] = None
    # -1 为"运行中"占位的约定值（parallel 模式的流式聚合用；对齐 pi 的
    # exitCode: -1 哨兵）；正常结束为进程退出码。
    exit_code: int = 0
    usage: SubagentUsage = field(default_factory=SubagentUsage)
    model: Optional[str] = None
    stop_reason: Optional[str] = None
    # agent 来源（package/user/project，取自会话注册表 AgentConfig.source_info），
    # 渲染器展示用；未知为 None。
    agent_source: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
