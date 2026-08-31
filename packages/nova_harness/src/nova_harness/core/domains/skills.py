"""Skill 运行时管理。

把 skill 的 **加载** 与 **运行时管理** 分离：

- ``resources/loaders/skills.py`` 负责从文件系统发现并解析 ``SKILL.md``；
- 本模块负责 skill 在 AgentSession 运行期的使用：
  生成系统提示词附录、展开 ``/skill:name`` 命令、解析 skill block、
  提供 slash 命令列表等。
"""

import re
from pathlib import Path
from typing import List, Mapping, Optional

from nova_harness.core.types.extensions import ExtensionCommand
from nova_harness.core.types.resources.skills import ParsedSkillBlock, Skill
from nova_harness.core.utils.frontmatter import strip_frontmatter
from nova_harness.core.utils.name_sets import apply_name_list
from nova_harness.core.utils.skills import (
    _escape_xml,
    format_skills_for_prompt,
    list_skill_commands,
)


def is_package_skill(skill: Skill) -> bool:
    """skill 是否来自已安装包——白名单只约束包内 skill。"""
    info = getattr(skill, "source_info", None)
    return info is not None and info.origin == "package"


def filter_skills_by_whitelist(
    skills: Mapping[str, Skill],
    allowed_names: Optional[List[str]],
) -> dict[str, Skill]:
    """按来源分治的 skill 名单过滤（纯人格裁剪，不承担安全职责）。

    名单只约束**包内 skill**（``origin="package"``——agent 作者裁剪每轮
    自动注入系统提示附录的包内 skill，token 卫生 + 人格聚焦）；用户级 /
    项目级 / 显式路径 / 扩展贡献的 skill 始终放行（随时可加性——用户与
    团队的技能库不需要 agent 作者授权；项目级安全边界归 project trust）。

    三态（包内管辖面内）：``None`` = 全放；``[]`` = 包内全禁；
    名单 = 包内仅列名（支持 ``!`` 排除，词汇归 ``name_sets``）。
    """
    if allowed_names is None:
        return dict(skills)
    package_names = [n for n, s in skills.items() if is_package_skill(s)]
    surviving_package = apply_name_list(package_names, allowed_names)
    return {
        name: skill
        for name, skill in skills.items()
        if not is_package_skill(skill) or name in surviving_package
    }


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
    "filter_skills_by_whitelist",
    "format_skills_for_prompt",
    "expand_skill_command",
    "is_package_skill",
    "parse_skill_block",
    "list_skill_commands",
]
