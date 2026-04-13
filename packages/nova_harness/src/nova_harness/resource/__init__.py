# __init__.py

"""
资源加载器包。

提供提示词模板加载、解析和管理功能。
"""

# 类型定义
from .types import (
    PromptTemplate,
    ParsedFrontmatter,
    LoadPromptTemplatesOptions,
    DefaultResourceLoaderOptions,
)

# 诊断相关
from .diagnostics import (
    ResourceCollision,
    ResourceDiagnostic,
)

# 加载器
from .loader import (
    ResourceLoader,
    DefaultResourceLoader,
)

# 提示词模板函数
from .prompt_templates import (
    load_prompt_templates,
    expand_prompt_template,
    parse_command_args,
    substitute_args,
)

# 工具函数
from .utils import (
    parse_frontmatter,
    strip_frontmatter,
    extract_frontmatter,
    normalize_newlines,
)

__all__ = [
    # 类型
    "PromptTemplate",
    "ParsedFrontmatter",
    "LoadPromptTemplatesOptions",
    "DefaultResourceLoaderOptions",
    # 诊断
    "ResourceCollision",
    "ResourceDiagnostic",
    # 加载器
    "ResourceLoader",
    "DefaultResourceLoader",
    # 提示词模板函数
    "load_prompt_templates",
    "expand_prompt_template",
    "parse_command_args",
    "substitute_args",
    # 工具函数
    "parse_frontmatter",
    "strip_frontmatter",
    "extract_frontmatter",
    "normalize_newlines",
]