"""资源加载相关类型。"""

from nova_harness.core.types.resources.diagnostics import (
    ResourceCollision,
    ResourceDiagnostic,
)
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions
from nova_harness.core.types.resources.paths import (
    ResourceExtensionPathEntry,
    ResourceExtensionPathMetadata,
    ResourceExtensionPaths,
)
from nova_harness.core.types.resources.prompts import (
    LoadPromptTemplatesOptions,
    ParsedFrontmatter,
    PromptTemplate,
)

__all__ = [
    "DefaultResourceLoaderOptions",
    "LoadPromptTemplatesOptions",
    "ParsedFrontmatter",
    "PromptTemplate",
    "ResourceCollision",
    "ResourceDiagnostic",
    "ResourceExtensionPathEntry",
    "ResourceExtensionPathMetadata",
    "ResourceExtensionPaths",
]
