"""扩展贡献的资源路径类型。"""

from typing import List, Literal, Optional

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field


class ResourceExtensionPathMetadata(NovaBaseModel):
    """扩展贡献的资源路径元数据。"""

    source: str = "extension"
    scope: Literal["temporary"] = "temporary"
    origin: Literal["top-level"] = "top-level"
    base_dir: Optional[str] = None


class ResourceExtensionPathEntry(NovaBaseModel):
    """扩展贡献的单个资源路径。"""

    path: str
    extension_path: Optional[str] = None
    metadata: ResourceExtensionPathMetadata = Field(
        default_factory=ResourceExtensionPathMetadata
    )


class ResourceExtensionPaths(NovaBaseModel):
    """扩展通过 resources_discover 贡献的资源路径集合。

    注意：tools 有独立的包管理与发现链路，不再通过扩展贡献临时路径。
    """

    skill_paths: List[ResourceExtensionPathEntry] = Field(default_factory=list)
    prompt_paths: List[ResourceExtensionPathEntry] = Field(default_factory=list)
    theme_paths: List[ResourceExtensionPathEntry] = Field(default_factory=list)


__all__ = [
    "ResourceExtensionPathMetadata",
    "ResourceExtensionPathEntry",
    "ResourceExtensionPaths",
]
