"""提示词模板资源类型。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Union

from nova_ai.types.base_model import NovaBaseModel

from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.package import ResolvedResource


class PromptTemplate(NovaBaseModel):
    """Represents a prompt template loaded from a markdown file."""

    name: str = ""
    description: str = ""
    argument_hint: Optional[str] = None
    content: str = ""
    source: str = "user"
    file_path: str = ""
    source_info: Optional[SourceInfo] = None


@dataclass(frozen=True)
class ParsedFrontmatter:
    """Markdown frontmatter 解析结果（内部解析中间态，不跨 JSON 边界）。"""

    frontmatter: dict[str, Any]
    body: str


@dataclass
class LoadPromptTemplatesOptions:
    """Options for loading prompt templates（程序内传参对象）。"""

    # Working directory for project-local templates. Default: os.getcwd()
    cwd: Optional[Union[str, Path]] = None
    # Agent config directory for global templates. Default: ~/.nova/agent
    # （散养模板经 resolver 在 <agent_dir>/backend/prompts 自动发现）
    agent_dir: Optional[Union[str, Path]] = None
    # Explicit prompt template paths (files or directories)
    prompt_paths: Optional[List[Union[str, Path]]] = None
    # Resolver 提供的资源项（含完整 PathMetadata），优先级高于路径推测
    resolved_resources: Optional[List[ResolvedResource]] = None
    # 扩展通过 resources_discover 贡献路径的来源信息列表，用于前缀匹配
    extension_source_infos: Optional[List[SourceInfo]] = None


__all__ = [
    "PromptTemplate",
    "ParsedFrontmatter",
    "LoadPromptTemplatesOptions",
]
