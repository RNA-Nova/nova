"""包 manifest 与元数据类型（JSON 边界 Pydantic）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

from nova_harness.core.types.package_manager.enums import SourceScope
from nova_harness.core.types.package_manager.resolution import PackageFilter


@dataclass
class UninstallResult:
    """卸载结果。

    默认只卸载 user scope；``local=True`` 时卸载 project scope。
    ``messages`` 用于承载 project scope 因未信任而被跳过等提示信息。
    """

    removed: bool
    messages: List[str] = field(default_factory=list)


class PackageMetadata(NovaBaseModel):
    """Metadata for an installed package.

    Package metadata is derived from the package manifest (``pyproject.toml``)
    and the source spec. It is no longer persisted separately in
    ``packages.json``; instead, the list of installed source specs lives in
    ``settings.json`` under the ``packages`` key.
    """

    name: str
    version: str
    description: str
    source: str
    install_path: str
    installed_at: str
    author: str = ""
    package_name: str = ""
    editable: bool = False
    dependencies: List[str] = Field(default_factory=list)
    filtered: bool = False
    filters: PackageFilter = Field(default_factory=PackageFilter)


class ResourceMetadata(NovaBaseModel):
    """Package 内部单个资源（agent/tool/skill/extension）的元数据。"""

    name: str
    resource_type: str
    source: str
    install_path: str


class PackageView(NovaBaseModel):
    """A view of a package with its agents, tools, skills, extensions, prompts and themes."""

    name: str
    version: str
    description: str
    agents: List[ResourceMetadata]
    tools: List[ResourceMetadata]
    skills: List[ResourceMetadata]
    extensions: List[ResourceMetadata] = Field(default_factory=list)
    prompts: List[ResourceMetadata] = Field(default_factory=list)
    themes: List[ResourceMetadata] = Field(default_factory=list)


class ConfiguredPackage(NovaBaseModel):
    """Settings 中配置的一个包源，无论是否已安装。"""

    source: str
    scope: SourceScope
    filtered: bool = False
    installed_path: Optional[str] = None


class PackageSource(NovaBaseModel):
    """解析后的 package source 规范。"""

    type: Literal["path", "git"]
    spec: str  # 原始 / 规范化的 spec 字符串
    editable: bool = False  # path 来源的安装模式
    path: Optional[str] = None  # path: 绝对或相对路径
    remote_url: Optional[str] = None  # git: clone URL
    host: Optional[str] = None  # git: 用于缓存目录的 host
    repo_path: Optional[str] = None  # git: "user/repo" 部分
    ref: Optional[str] = None  # git: branch/tag/commit


class NovaManifest(NovaBaseModel):
    """``pyproject.toml`` 中 ``[tool.nova]`` 配置段。"""

    agents: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    extensions: Optional[List[str]] = None
    prompts: Optional[List[str]] = None
    themes: Optional[List[str]] = None
    auto_install_dependencies: bool = True


class PackageManifest(NovaBaseModel):
    """规范化后的 package manifest，数据来自 ``pyproject.toml``。"""

    name: Optional[str] = None
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    nova: Optional[NovaManifest] = None


class PackageUpdate(NovaBaseModel):
    """描述一个可更新的已配置包。"""

    source: str
    display_name: str
    type: Literal["git"] = "git"
    scope: SourceScope


__all__ = [
    "PackageMetadata",
    "PackageView",
    "ResourceMetadata",
    "PackageSource",
    "NovaManifest",
    "PackageManifest",
    "PackageUpdate",
    "UninstallResult",
]
