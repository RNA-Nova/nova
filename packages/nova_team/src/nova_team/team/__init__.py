# definition/__init__.py

"""
TeamDefinitor - 主从多智能体管理包（两级存储）

存储层级：
- PROJECT: {cwd}/.kimi/mounts.json（优先）
- GLOBAL: {agent_dir}/mounts.json（兜底）

示例：
    >>> from teamdefinitor import TeamDefinitor
    >>> tm = TeamDefinitor("./team", cwd=os.getcwd(), agent_dir=get_agent_dir())
    >>> 
    >>> # 自动使用 PROJECT 或 GLOBAL 的 mounts
    >>> master_prompt = tm.build_master()
    >>> coder_prompt = tm.build_subagent("coder")
"""

from .definitor import TeamDefinitor
from .types import (
    MasterMountEntry,
    MountsData,
    SubagentMountEntry,
)

__version__ = "1.0.0"
__all__ = [
    "TeamDefinitor",
    "MountsData",
    "MasterMountEntry",
    "SubagentMountEntry",
]