"""包管理器枚举与字面量类型。"""

from __future__ import annotations

from enum import Enum
from typing import Literal


class SourceScope(str, Enum):
    """资源来源的作用域。"""

    USER = "user"
    PROJECT = "project"
    TEMPORARY = "temporary"


class SourceOrigin(str, Enum):
    """资源来源的起源类型。"""

    PACKAGE = "package"
    TOP_LEVEL = "top-level"


class ResourceType(str, Enum):
    """可被 PackageResolver 解析的包内资源类型。

    注意：context files 不属于包内资源，它们通过从工作目录向上遍历发现，
    因此不在这个枚举中。
    """

    EXTENSIONS = "extensions"
    SKILLS = "skills"
    PROMPTS = "prompts"
    THEMES = "themes"
    TOOLS = "tools"
    AGENTS = "agents"


RESOURCE_TYPE_DIRS: dict[ResourceType, str] = {
    ResourceType.EXTENSIONS: "extensions",
    ResourceType.SKILLS: "skills",
    ResourceType.PROMPTS: "prompts",
    ResourceType.THEMES: "themes",
    ResourceType.TOOLS: "tools",
    ResourceType.AGENTS: "agents",
}


MissingSourceAction = Literal["install", "skip", "error"]


__all__ = [
    "SourceScope",
    "SourceOrigin",
    "ResourceType",
    "RESOURCE_TYPE_DIRS",
    "MissingSourceAction",
]
