"""运行时世界（读）：资源路径解析与发现。

- ``resolver``: ``PackageResolver`` —— 按 settings、已安装包与自动发现
  规则输出带来源元数据的资源路径；
- ``discovery``: 资源自动发现与 override 模式（``!`` / ``+`` / ``-``）匹配。
"""

from nova_harness.core.package.resolve.resolver import (
    PackageResolver,
    build_path_metadata,
    resource_precedence_rank,
    sort_resolved_resources,
)

__all__ = [
    "PackageResolver",
    "build_path_metadata",
    "resource_precedence_rank",
    "sort_resolved_resources",
]
