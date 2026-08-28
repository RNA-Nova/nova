"""Agent 配置资源类型（``agents/<name>.yaml`` 组合声明的解析产物）。

AgentConfig 是**运行时选配状态的初始值**（纯声明快照）：名单字段的语义
（三态 + ``!`` 排除）由消费点经 ``core/utils/name_sets.py`` 裁决，
本类型只负责原样承载。
"""

from typing import Any, Dict, List, Optional

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.resources.tools import ToolInfo


class DynamicContext(NovaBaseModel):
    """
    动态上下文数据（运行时注入）。

    包含每次请求可能变化的动态信息：
    - cwd: 当前工作目录（环境段——随后端切换变）
    - timestamp: 时间戳（可选）
    - session_id: 会话标识（可选）
    - custom_vars: 自定义动态变量字典
    - backend / environment_id / shell / workspace_roots / permission / network：
      执行后端环境段字段（executor 接入——切换后经 _sync_system_prompt 重建）
    """

    cwd: Optional[str] = None
    timestamp: Optional[str] = None
    session_id: Optional[str] = None
    custom_vars: Dict[str, Any] = Field(default_factory=dict)
    # 执行后端环境段（executor 接入）
    backend: Optional[str] = None
    environment_id: Optional[str] = None
    shell: Optional[str] = None
    workspace_roots: Optional[List[str]] = None
    permission: Optional[str] = None
    network: Optional[str] = None

    def get_cwd(self, fallback: str = ".") -> str:
        """获取 cwd，未设置时返回 fallback。"""
        return self.cwd or fallback


class Section(NovaBaseModel):
    """Markdown 内容片段。"""

    name: str
    order: int
    content: str
    source: str = ""


class AgentConfig(NovaBaseModel):
    """Agent 配置快照（纯声明，无语义解释）。

    名单字段统一**三态**（``None``=全放不设防 / ``[]``=全禁 / 名单），
    名单条目支持 ``!name`` 排除（``+``/``-`` 强制级词汇保留——见
    ``name_sets`` 模块 docstring）。``tools`` 条目为 ToolInfo（dict 形态
    可带描述覆盖），``!`` 排除仅以字符串条目表达。
    """

    name: str
    agent_dir: str

    description: Optional[str] = None
    model: Optional[str] = None
    # 来源（包/用户/项目……）——收集层 resolver 的 provenance 透传
    source_info: Optional[SourceInfo] = None
    # persona 素材引用（相对路径或注册名——persona 升格后由 PersonaManager 装配）
    persona: List[str] = Field(default_factory=list)
    sections: List[Section] = Field(default_factory=list)
    tools: Optional[List[ToolInfo]] = None
    # skill 包内裁剪名单（非空仅裁 origin=package 的包内 skill，
    # 用户级/项目级/显式路径 skill 始终放行——"随时可加性"）
    skills: Optional[List[str]] = None
    extensions: Optional[List[str]] = None
    user_tools: Optional[List[str]] = None
    # 命令允许集（裁剪扩展注册的命令；用户层另有 settings 排除集）
    commands: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "agent_dir": self.agent_dir,
            "has_description": self.description is not None,
            "sections_count": len(self.sections),
            "tools": [t.name for t in self.tools] if self.tools is not None else None,
            "skills": list(self.skills) if self.skills is not None else None,
            "extensions": (
                list(self.extensions) if self.extensions is not None else None
            ),
            "user_tools": (
                list(self.user_tools) if self.user_tools is not None else None
            ),
            "commands": list(self.commands) if self.commands is not None else None,
        }


__all__ = [
    "DynamicContext",
    "Section",
    "AgentConfig",
]
