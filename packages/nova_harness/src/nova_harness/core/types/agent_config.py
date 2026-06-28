"""
系统提示词构建相关的数据类型。
"""

from typing import Any, Dict, List, Optional

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field


class DynamicContext(NovaBaseModel):
    """
    动态上下文数据（运行时注入）。

    包含每次请求可能变化的动态信息：
    - cwd: 当前工作目录
    - timestamp: 时间戳（可选）
    - session_id: 会话标识（可选）
    - custom_vars: 自定义动态变量字典
    """

    cwd: Optional[str] = None
    timestamp: Optional[str] = None
    session_id: Optional[str] = None
    custom_vars: Dict[str, Any] = Field(default_factory=dict)

    def get_cwd(self, fallback: str = ".") -> str:
        """获取 cwd，未设置时返回 fallback。"""
        return self.cwd or fallback


class ToolInfo(NovaBaseModel):
    """工具定义。"""

    name: str
    description: str


class Section(NovaBaseModel):
    """Markdown 内容片段。"""

    name: str
    order: int
    content: str
    source: str = ""


class AgentConfig(NovaBaseModel):
    """Agent 配置快照。"""

    name: str
    agent_dir: str

    description: Optional[str] = None
    model: Optional[str] = None
    subagents: List[str] = Field(default_factory=list)
    sections: List[Section] = Field(default_factory=list)
    tools: List[ToolInfo] = Field(default_factory=list)

    setup_content: Optional[str] = None
    user_sections: List[Section] = Field(default_factory=list)

    @property
    def has_setup(self) -> bool:
        return self.setup_content is not None and len(self.setup_content.strip()) > 0

    @property
    def has_user_data(self) -> bool:
        return len(self.user_sections) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "agent_dir": self.agent_dir,
            "has_description": self.description is not None,
            "sections_count": len(self.sections),
            "tools_count": len(self.tools),
            "has_setup": self.has_setup,
            "user_sections_count": len(self.user_sections),
            "tools": [t.name for t in self.tools],
        }


__all__ = [
    "DynamicContext",
    "ToolInfo",
    "Section",
    "AgentConfig",
]
