# definition/types.py

"""
TeamDefinitor 数据类型定义（dynamic_context 全局化）
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mashumaro.mixins.json import DataClassJSONMixin
from ..definition import DynamicContext

@dataclass
class SubagentMountEntry(DataClassJSONMixin):
    """Subagent 挂载配置（无 dynamic_context）."""
    enabled: bool = True
    include_dynamic: bool = True
    include_tools: bool = True
    include_user: bool = False
    selected_tools: List[str] = field(default_factory=list)


@dataclass
class MasterMountEntry(DataClassJSONMixin):
    """Master 挂载配置（无 dynamic_context）."""
    include_dynamic: bool = True
    include_tools: bool = True
    include_user: bool = True
    selected_tools: List[str] = field(default_factory=list)
    inject_subagents_desc: bool = True


@dataclass
class MountsData(DataClassJSONMixin):
    """
    mounts.json 数据结构.
    
    dynamic_context 提升到全局，master 和 subagents 共用.
    构建时可传入动态上下文覆盖.
    """
    dynamic_context: Optional[DynamicContext] = None
    master: MasterMountEntry = field(default_factory=MasterMountEntry)
    subagents: Dict[str, SubagentMountEntry] = field(default_factory=dict)