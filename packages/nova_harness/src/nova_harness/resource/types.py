import os
from typing import List, Literal, Optional, Any

from ..config import get_agent_dir
from dataclasses import dataclass, field
from mashumaro.mixins.json import DataClassJSONMixin

@dataclass
class PromptTemplate(DataClassJSONMixin):
    """Represents a prompt template loaded from a markdown file."""
    name: str = ""
    description: str = ""
    content: str = ""
    source: Literal["user", "project", "path"] = "user"
    file_path: str = ""

@dataclass
class ParsedFrontmatter:
    frontmatter: dict[str, Any]
    body: str

@dataclass
class LoadPromptTemplatesOptions(DataClassJSONMixin):
    """Options for loading prompt templates."""
    # Working directory for project-local templates. Default: os.getcwd()
    cwd: Optional[str] = None
    # Agent config directory for global templates. Default: from get_prompts_dir()
    agent_dir: Optional[str] = None
    # Explicit prompt template paths (files or directories)
    prompt_paths: Optional[List[str]] = None
    # Include default prompt directories. Default: True
    include_defaults: bool = True

@dataclass
class DefaultResourceLoaderOptions(DataClassJSONMixin):
    """资源加载器配置选项（仅保留 prompt templates 相关）"""
    
    cwd: Optional[str] = field(default_factory=os.getcwd)
    agent_dir: Optional[str] = field(default_factory=get_agent_dir)
    additional_prompt_template_paths: Optional[List[str]] = field(default_factory=list)
    no_prompt_templates: bool = False