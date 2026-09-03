"""Skill 格式化工具函数。

本模块仅包含不依赖 harness 运行时的纯工具函数，
供 ``harness/skills.py`` 与 ``harness/system_prompt`` 使用，
避免底层资源加载器/系统提示词层向上依赖 harness。
"""

from typing import Iterable

from nova_harness.core.types.extensions import ExtensionCommand
from nova_harness.core.types.resources.skills import Skill


def _escape_xml(value: str) -> str:
    """对 XML 属性/文本进行简单转义。"""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def format_skills_for_prompt(
    skills: Iterable[Skill], has_read_tool: bool = True
) -> str:
    """
    把可用 skill 格式化为系统提示词 XML 片段。

    仅当模型拥有 ``read`` 工具时才应该注入该片段；
    ``disable_model_invocation=True`` 的 skill 不会被注入，
    它们只能通过 ``/skill:name`` 显式调用。

    输出格式遵循 Agent Skills 标准：
    https://agentskills.io/integrate-skills
    """
    if not has_read_tool:
        return ""

    visible = [s for s in skills if not s.disable_model_invocation]
    if not visible:
        return ""

    lines = [
        "\n\nThe following skills provide specialized instructions for specific tasks.",
        "Use the read tool to load a skill's file when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill directory "
        "(parent of SKILL.md / dirname of the path) and use that absolute path in tool commands.",
        "",
        "<available_skills>",
    ]

    for skill in visible:
        lines.append("  <skill>")
        lines.append(f"    <name>{_escape_xml(skill.name)}</name>")
        lines.append(f"    <description>{_escape_xml(skill.description)}</description>")
        lines.append(f"    <location>{_escape_xml(skill.file_path)}</location>")
        lines.append("  </skill>")

    lines.append("</available_skills>")

    return "\n".join(lines)


def list_skill_commands(skills: dict[str, Skill]) -> list[ExtensionCommand]:
    """把 skill 列表转换为 slash 命令列表（invocation name 为 ``skill:name``）。"""
    return [
        ExtensionCommand(
            name=f"skill:{skill.name}",
            description=skill.description,
        )
        for skill in skills.values()
    ]


__all__ = [
    "_escape_xml",
    "format_skills_for_prompt",
    "list_skill_commands",
]
