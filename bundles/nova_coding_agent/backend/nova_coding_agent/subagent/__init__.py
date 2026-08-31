"""Nova 子智能体（subagent）公共实现。

提供 run_subagent_single/parallel/chain 等执行能力，供 ``subagent`` 工具
或其它调用方使用。agent 解析不在本包——消费会话 agents 注册表
（``ToolExecContext.agents``），发现管线零重复。
"""

from nova_coding_agent.subagent.runner import (
    format_parallel_output,
    run_subagent_chain,
    run_subagent_parallel,
    run_subagent_single,
)
from nova_coding_agent.subagent.types import (
    SubagentCall,
    SubagentMode,
    SubagentResult,
    SubagentUsage,
)

__all__ = [
    "SubagentCall",
    "SubagentMode",
    "SubagentResult",
    "SubagentUsage",
    "format_parallel_output",
    "run_subagent_chain",
    "run_subagent_parallel",
    "run_subagent_single",
]
