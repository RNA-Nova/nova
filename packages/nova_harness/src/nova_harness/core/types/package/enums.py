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
    TOOLS = "tools"
    AGENTS = "agents"
    USER_TOOLS = "user_tools"
    PERSONAS = "personas"


RESOURCE_TYPE_DIRS: dict[ResourceType, str] = {
    ResourceType.EXTENSIONS: "extensions",
    ResourceType.SKILLS: "skills",
    ResourceType.PROMPTS: "prompts",
    ResourceType.TOOLS: "tools",
    ResourceType.AGENTS: "agents",
    ResourceType.USER_TOOLS: "user_tools",
    ResourceType.PERSONAS: "personas",
}

# 前后端分治（nova-tui/docs/frontend-backend-separation.md §9）：
# user/project 根下的后端散养资源统一归 ``backend/`` 半区目录。
BACKEND_HALF_DIR_NAME = "backend"

# 顶层（user/project 根）自动发现的扫描目录：散养资源归 ``<base>/backend/<type>``，
# agents 两半共享保持 ``<base>/agents`` 平级。包内约定发现（manifest 缺省目录
# 扫描）不受本表影响，仍用 RESOURCE_TYPE_DIRS。
# tools / user_tools 本就不做顶层自动发现，本表不含其条目。
TOP_LEVEL_RESOURCE_TYPE_DIRS: dict[ResourceType, str] = {
    ResourceType.EXTENSIONS: f"{BACKEND_HALF_DIR_NAME}/extensions",
    ResourceType.SKILLS: f"{BACKEND_HALF_DIR_NAME}/skills",
    ResourceType.PROMPTS: f"{BACKEND_HALF_DIR_NAME}/prompts",
    ResourceType.PERSONAS: f"{BACKEND_HALF_DIR_NAME}/personas",
    ResourceType.AGENTS: "agents",
}


MissingSourceAction = Literal["install", "skip", "error"]


__all__ = [
    "SourceScope",
    "SourceOrigin",
    "ResourceType",
    "RESOURCE_TYPE_DIRS",
    "BACKEND_HALF_DIR_NAME",
    "TOP_LEVEL_RESOURCE_TYPE_DIRS",
    "MissingSourceAction",
]
