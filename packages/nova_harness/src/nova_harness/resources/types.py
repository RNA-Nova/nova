"""资源加载相关类型。"""

from nova_harness.resources.agents import AgentConfig, DynamicContext, Section
from nova_harness.resources.context_files import ContextFile
from nova_harness.resources.diagnostics import ResourceCollision, ResourceDiagnostic
from nova_harness.resources.extension_paths import (
    ResourceExtensionPathEntry,
    ResourceExtensionPathMetadata,
    ResourceExtensionPaths,
)
from nova_harness.resources.options import DefaultResourceLoaderOptions
from nova_harness.resources.personas import Persona
from nova_harness.resources.prompts import (
    LoadPromptTemplatesOptions,
    ParsedFrontmatter,
    PromptTemplate,
)
from nova_harness.resources.selection import CapabilitySelection
from nova_harness.resources.skills import ParsedSkillBlock, Skill
from nova_harness.resources.tools import ToolDefinition, ToolInfo
from nova_harness.resources.user_tools import UserToolDefinition, UserToolInfo

__all__ = [
    "AgentConfig",
    "CapabilitySelection",
    "ContextFile",
    "DefaultResourceLoaderOptions",
    "DynamicContext",
    "LoadPromptTemplatesOptions",
    "ParsedFrontmatter",
    "ParsedSkillBlock",
    "Persona",
    "PromptTemplate",
    "ResourceCollision",
    "ResourceDiagnostic",
    "ResourceExtensionPathEntry",
    "ResourceExtensionPathMetadata",
    "ResourceExtensionPaths",
    "Section",
    "UserToolDefinition",
    "UserToolInfo",
    "Skill",
    "ToolDefinition",
    "ToolInfo",
]
