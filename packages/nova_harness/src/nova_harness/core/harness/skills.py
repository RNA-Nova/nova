"""Skill 运行时管理。

把 skill 的 **加载** 与 **运行时管理** 分离：

- ``resources/loaders/skills.py`` 负责从文件系统发现并解析 ``SKILL.md``；
- 本模块负责 skill 在 AgentSession 运行期的使用：
  生成系统提示词附录、展开 ``/skill:name`` 命令、解析 skill block、
  提供 slash 命令列表等。
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional

from nova_harness.core.types.extensions import ExtensionCommand
from nova_harness.core.types.skills import Skill
from nova_harness.core.utils.frontmatter import strip_frontmatter
from nova_harness.core.utils.skills import format_skills_for_prompt, list_skill_commands


def _escape_xml(value: str) -> str:
    """对 XML 属性/文本进行简单转义（与 TypeScript 侧保持一致）。"""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def expand_skill_command(text: str, skills: Mapping[str, Skill]) -> str:
    """
    展开 ``/skill:name args`` 为 XML skill block。

    如果 skill 不存在、读取失败或 skill 命令被禁用，则原样返回 ``text``。
    """
    if not text.startswith("/skill:"):
        return text

    space_index = text.find(" ")
    if space_index == -1:
        skill_name = text[len("/skill:") :]
        args = ""
    else:
        skill_name = text[len("/skill:") : space_index]
        args = text[space_index + 1 :].strip()

    skill = skills.get(skill_name)
    if skill is None:
        return text

    try:
        content = Path(skill.file_path).read_text(encoding="utf-8")
    except (IOError, UnicodeDecodeError):
        return text

    body = strip_frontmatter(content).strip()
    block = (
        f'<skill name="{_escape_xml(skill.name)}" '
        f'location="{_escape_xml(skill.file_path)}">\n'
        f"References are relative to {skill.base_dir}.\n\n"
        f"{body}\n"
        f"</skill>"
    )

    if args:
        return f"{block}\n\n{args}"
    return block


@dataclass(frozen=True)
class ParsedSkillBlock:
    """从用户消息中解析出的 skill block。"""

    name: str
    location: str
    content: str
    user_message: Optional[str] = None


_SKILL_BLOCK_RE = re.compile(
    r'^<skill name="([^"]+)" location="([^"]+)">\n([\s\S]*?)\n</skill>'
    r"(?:\n\n([\s\S]+))?$"
)


def parse_skill_block(text: str) -> Optional[ParsedSkillBlock]:
    """
    解析 XML skill block。

    返回 ``None`` 表示文本不是合法的 skill block。
    """
    match = _SKILL_BLOCK_RE.match(text)
    if not match:
        return None

    return ParsedSkillBlock(
        name=match.group(1),
        location=match.group(2),
        content=match.group(3),
        user_message=match.group(4).strip() if match.group(4) else None,
    )


class SkillManager:
    """Skill 运行时管理器。

    封装当前已加载 skill 的运行时操作，便于 AgentSession 统一调用。
    """

    def __init__(self, skills: Mapping[str, Skill]) -> None:
        self._skills: Mapping[str, Skill] = skills

    @property
    def skills(self) -> Mapping[str, Skill]:
        return self._skills

    def format_for_prompt(self, has_read_tool: bool = True) -> str:
        """生成系统提示词中的 skill 附录。"""
        return format_skills_for_prompt(self._skills.values(), has_read_tool)

    def expand_command(self, text: str) -> str:
        """展开 ``/skill:name`` 命令。"""
        return expand_skill_command(text, self._skills)

    def parse_block(self, text: str) -> Optional[ParsedSkillBlock]:
        """解析 XML skill block。"""
        return parse_skill_block(text)

    def list_commands(self) -> List[ExtensionCommand]:
        """返回 skill slash 命令列表。"""
        return list_skill_commands(self._skills)


__all__ = [
    "SkillManager",
    "ParsedSkillBlock",
    "format_skills_for_prompt",
    "expand_skill_command",
    "parse_skill_block",
    "list_skill_commands",
]
