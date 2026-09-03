"""包 manifest 与元数据类型（JSON 边界 Pydantic）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from nova_ai.types.base_model import NovaBaseModel
from nova_harness.core.types.package.enums import SourceScope
from nova_harness.core.types.package.resolution import PackageFilter
from pydantic import Field


@dataclass
class UninstallResult:
    """卸载结果。

    默认同时搜索 user 与 project 两个 scope 并卸载匹配的包；``local=True``
    时只卸载 project scope。``messages`` 承载每个 scope 的处置明细
    （移除、歧义跳过、底层 Python 包卸载），供 CLI/RPC 展示。
    """

    removed: bool
    messages: List[str] = field(default_factory=list)


class PackageMetadata(NovaBaseModel):
    """Metadata for an installed package.

    source/editable/package_name/installed_at 的权威来源是安装时写入的
    ``*.dist-info/`` 快照（对齐 pip 生态）；dist-info 缺失时从磁盘内容
    推导。name/version/dependencies 始终读副本 ``pyproject.toml``。
    """

    name: str
    version: str
    description: str
    source: str
    install_path: str
    author: str = ""
    package_name: str = ""
    editable: bool = False
    installed_at: str = ""
    dependencies: List[str] = Field(default_factory=list)
    # 包间依赖（nova 包名清单——与 name/version 同源，始终读副本 manifest）
    requires: List[str] = Field(default_factory=list)
    filtered: bool = False
    filters: PackageFilter = Field(default_factory=PackageFilter)


class ResourceMetadata(NovaBaseModel):
    """Package 内部单个资源（agent/tool/skill/extension）的元数据。"""

    name: str
    resource_type: str
    source: str
    install_path: str


class PackageView(NovaBaseModel):
    """A view of a package with its agents, tools, skills, extensions and prompts.

    ``install_path``/``scope`` 供 Node 层发现包的 ``ui/`` 资产（纯 UI 的
    B 型包没有任何资源条目，路径只能从这里来）；``scope`` 配合会话的
    project trust 决议门控 project 级 ui/ 代码加载。
    """

    name: str
    version: str
    description: str
    install_path: str
    scope: Literal["user", "project"] = "user"
    agents: List[ResourceMetadata]
    tools: List[ResourceMetadata]
    skills: List[ResourceMetadata]
    extensions: List[ResourceMetadata] = Field(default_factory=list)
    prompts: List[ResourceMetadata] = Field(default_factory=list)
    user_tools: List[ResourceMetadata] = Field(default_factory=list)
    personas: List[ResourceMetadata] = Field(default_factory=list)


class ConfiguredPackage(NovaBaseModel):
    """Settings 中配置的一个包源，无论是否已安装。"""

    source: str
    scope: SourceScope
    filtered: bool = False
    installed_path: Optional[str] = None


class PackageSource(NovaBaseModel):
    """解析后的 package source 规范。"""

    type: Literal["path", "git", "npm"]
    spec: str  # 原始 / 规范化的 spec 字符串
    editable: bool = False  # path 来源的安装模式
    path: Optional[str] = None  # path: 绝对或相对路径
    remote_url: Optional[str] = None  # git: clone URL
    host: Optional[str] = None  # git: 用于缓存目录的 host
    repo_path: Optional[str] = None  # git: "user/repo" 部分
    ref: Optional[str] = None  # git: branch/tag/commit
    npm_name: Optional[str] = None  # npm: 包名（可含 @scope/）
    npm_version: Optional[str] = None  # npm: 精确版本/range/部分版本；None = latest


class NovaManifest(NovaBaseModel):
    """``pyproject.toml`` 中 ``[tool.nova]`` 配置段。"""

    agents: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    extensions: Optional[List[str]] = None
    prompts: Optional[List[str]] = None
    user_tools: Optional[List[str]] = None
    personas: Optional[List[str]] = None
    auto_install_dependencies: bool = True
    # 二进制依赖（wheel 可装）：命令名 → PyPI 包名，如 {"rg": "ripgrep"}。
    # 安装时随 pip 依赖进入当前环境的 bin/，运行时经 resolve_binary 命中。
    binary_dependencies: Optional[Dict[str, str]] = None
    # 自管理二进制（框架注册表，无 wheel）：安装时按 pin 版本 + sha256
    # 下载到 ~/.nova/agent/bin/，如 ["fd"]。条目仅限框架注册表已知项。
    binary_managed_dependencies: Optional[List[str]] = None
    # 系统二进制要求（无 wheel，不代装）：安装时校验存在性、缺失警告。
    binary_system_dependencies: Optional[List[str]] = None
    # 包间依赖（nova 包名清单——非 Python/npm 依赖）：安装时校验被依赖包
    # 已安装（user/project 合并视图，任一 scope 命中即满足），缺失即拒绝
    # 安装；卸载时被其他包引用的包拒绝卸载。v1 只做约束校验，不做来源
    # 解析（无中心 registry，名字不携带安装来源信息）。
    requires: Optional[List[str]] = None


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
    type: Literal["git", "npm"] = "git"
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
