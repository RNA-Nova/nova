"""
资源加载类型。

对应原 `nova_harness.resource.types`。
"""

import os
from pathlib import Path
from typing import Any, List, Literal, Optional, Union

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field


class PromptTemplate(NovaBaseModel):
    """Represents a prompt template loaded from a markdown file."""

    name: str = ""
    description: str = ""
    content: str = ""
    source: Literal["user", "project", "path"] = "user"
    file_path: str = ""


class ParsedFrontmatter(NovaBaseModel):
    frontmatter: dict[str, Any]
    body: str


class LoadPromptTemplatesOptions(NovaBaseModel):
    """Options for loading prompt templates."""

    # Working directory for project-local templates. Default: os.getcwd()
    cwd: Optional[Union[str, Path]] = None
    # Agent config directory for global templates. Default: from get_prompts_dir()
    agent_dir: Optional[Union[str, Path]] = None
    # Explicit prompt template paths (files or directories)
    prompt_paths: Optional[List[Union[str, Path]]] = None
    # Include default prompt directories. Default: True
    include_defaults: bool = True


class DefaultResourceLoaderOptions(NovaBaseModel):
    """资源加载器配置选项（prompt templates + extensions）。"""

    model_config = NovaBaseModel.model_config.copy()
    model_config["arbitrary_types_allowed"] = True

    cwd: Optional[Union[str, Path]] = Field(default_factory=os.getcwd)
    agent_dir: Optional[Union[str, Path]] = None
    settings_manager: Optional[Any] = None
    model_registry: Optional[Any] = None
    additional_prompt_template_paths: Optional[List[Union[str, Path]]] = Field(
        default_factory=list
    )
    additional_extension_paths: Optional[List[str]] = Field(default_factory=list)
    additional_skill_paths: Optional[List[str]] = Field(default_factory=list)
    additional_theme_paths: Optional[List[str]] = Field(default_factory=list)
    additional_tool_paths: Optional[List[str]] = Field(default_factory=list)
    no_extensions: bool = False
    no_tools: bool = False
    no_prompt_templates: bool = False
    no_skills: bool = False
    no_themes: bool = True
    event_bus: Optional[Any] = None
    # 用于把 NovaExtensionAPI 的创建推迟到 core 层，避免 resources 向上依赖 core
    extension_api_factory: Optional[Any] = None


class ResourceExtensionPathMetadata(NovaBaseModel):
    """扩展贡献的资源路径元数据。"""

    source: str = "extension"
    scope: Literal["temporary"] = "temporary"
    origin: Literal["top-level"] = "top-level"
    base_dir: Optional[str] = None


class ResourceExtensionPathEntry(NovaBaseModel):
    """扩展贡献的单个资源路径。"""

    path: str
    metadata: ResourceExtensionPathMetadata = Field(
        default_factory=ResourceExtensionPathMetadata
    )


class ResourceExtensionPaths(NovaBaseModel):
    """扩展通过 resources_discover 贡献的资源路径集合。"""

    skill_paths: List[ResourceExtensionPathEntry] = Field(default_factory=list)
    prompt_paths: List[ResourceExtensionPathEntry] = Field(default_factory=list)
    theme_paths: List[ResourceExtensionPathEntry] = Field(default_factory=list)
    tool_paths: List[ResourceExtensionPathEntry] = Field(default_factory=list)


__all__ = [
    "PromptTemplate",
    "ParsedFrontmatter",
    "LoadPromptTemplatesOptions",
    "DefaultResourceLoaderOptions",
    "ResourceExtensionPathMetadata",
    "ResourceExtensionPathEntry",
    "ResourceExtensionPaths",
]
