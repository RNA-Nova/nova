"""Skill 类型定义。

Skill 是一份可被发现的 Markdown 指令文件，通常命名为 ``SKILL.md``。
"""

from typing import Optional

from nova_ai.types.base_model import NovaBaseModel


class Skill(NovaBaseModel):
    """一个已加载的 skill。"""

    name: str
    description: str
    file_path: str
    base_dir: str
    disable_model_invocation: bool = False
    source_label: str = "unknown"


class SkillFrontmatter(NovaBaseModel):
    """SKILL.md  frontmatter 的规范子集。"""

    name: Optional[str] = None
    description: Optional[str] = None
    disable_model_invocation: bool = False


__all__ = ["Skill", "SkillFrontmatter"]
