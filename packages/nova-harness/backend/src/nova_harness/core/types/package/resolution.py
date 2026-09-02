"""包资源解析结果类型（运行时 dataclass 容器）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from nova_harness.core.types.resources.diagnostics import ResourceDiagnostic

from nova_harness.core.types.package.enums import SourceOrigin, SourceScope


@dataclass
class PathMetadata:
    """描述一个资源路径的来源信息。"""

    source: str
    scope: SourceScope
    origin: SourceOrigin
    base_dir: Optional[str] = None


@dataclass
class ResolvedResource:
    """解析后的单个资源项。"""

    path: str
    enabled: bool
    metadata: PathMetadata


@dataclass
class ResolvedPaths:
    """PackageResolver 的解析结果。"""

    extensions: List[ResolvedResource] = field(default_factory=list)
    skills: List[ResolvedResource] = field(default_factory=list)
    prompts: List[ResolvedResource] = field(default_factory=list)
    tools: List[ResolvedResource] = field(default_factory=list)
    agents: List[ResolvedResource] = field(default_factory=list)
    user_tools: List[ResolvedResource] = field(default_factory=list)
    personas: List[ResolvedResource] = field(default_factory=list)
    diagnostics: List["ResourceDiagnostic"] = field(default_factory=list)


@dataclass
class PackageFilter:
    """包级别的资源过滤器，对应 settings 中 package dict 的过滤字段。

    支持 ``extensions`` / ``skills`` / ``prompts`` / ``tools`` /
    ``agents`` / ``user_tools`` / ``personas`` 七类包内资源的过滤。
    字段为 ``None`` 表示不过滤（启用该类型全部资源）；空列表表示禁用
    该类型全部资源；非空列表表示仅启用列表中指定的资源名/路径模式。

    ``autoload=False`` 是跨 scope 的 delta 语义：project scope 的同 identity
    条目不再是独立覆盖，而是在 user scope 自动加载结果的基础上做局部翻转
    （``-path`` / ``!pattern`` 禁用、``+path`` / 普通 pattern 启用，仅作用于
    匹配到的资源，未提及的资源由 user 条目决定）。

    注意：context files 不属于包内资源，它们通过从工作目录向上遍历发现，
    因此不在本过滤器范围内。
    """

    extensions: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    prompts: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    agents: Optional[List[str]] = None
    user_tools: Optional[List[str]] = None
    personas: Optional[List[str]] = None
    autoload: Optional[bool] = None


__all__ = [
    "PathMetadata",
    "ResolvedResource",
    "ResolvedPaths",
    "PackageFilter",
]
