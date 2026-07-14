"""Nova 子智能体（subagent）公共实现。

提供 discover_agents、run_subagent_single/parallel/chain 等能力，
供 ``subagent`` 工具或其它调用方使用。
"""

from nova_coding_agent.subagent.runner import (
    discover_agents,
    format_parallel_output,
    run_subagent_chain,
    run_subagent_parallel,
    run_subagent_single,
)
from nova_coding_agent.subagent.types import (
    AgentInfo,
    AgentScope,
    SubagentCall,
    SubagentMode,
    SubagentResult,
    SubagentUsage,
)

__all__ = [
    "AgentInfo",
    "AgentScope",
    "SubagentCall",
    "SubagentMode",
    "SubagentResult",
    "SubagentUsage",
    "discover_agents",
    "format_parallel_output",
    "run_subagent_chain",
    "run_subagent_parallel",
    "run_subagent_single",
]
