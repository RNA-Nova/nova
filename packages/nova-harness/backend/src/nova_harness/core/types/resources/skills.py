"""Skill 资源类型定义。

Skill 是一份可被发现的 Markdown 指令文件，通常命名为 ``SKILL.md``。
"""

from dataclasses import dataclass
from typing import Optional

from nova_ai.types.base_model import NovaBaseModel
from nova_harness.core.types.extensions import SourceInfo


class Skill(NovaBaseModel):
    """一个已加载的 skill。"""

    name: str
    description: str
    file_path: str
    base_dir: str
    disable_model_invocation: bool = False
    source_label: str = "unknown"
    source_info: Optional[SourceInfo] = None


@dataclass(frozen=True)
class ParsedSkillBlock:
    """从用户消息中解析出的 skill block。"""

    name: str
    location: str
    content: str
    user_message: Optional[str] = None


__all__ = ["Skill", "ParsedSkillBlock"]
