"""source 领域（两个世界共享）：source spec 模型与获取。

- ``spec``: source spec 解析、package identity、跨 scope 去重（纯函数，无 IO）；
- ``resolver``: ``SourceResolver`` —— 把 source 物化为本地目录（git clone/pull）。
"""

from nova_harness.core.package.source.resolver import SourceResolver
from nova_harness.core.package.source.spec import (
    PackageSource,
    PackageSourceCollection,
    ResolvedScopedSources,
    get_package_identity,
    get_package_source_string,
    merge_package_source_specs,
    normalize_package_source_for_settings,
    parse_package_source_spec,
    parse_source,
    resolve_package_source_from_settings,
)

__all__ = [
    "PackageSource",
    "PackageSourceCollection",
    "ResolvedScopedSources",
    "SourceResolver",
    "get_package_identity",
    "get_package_source_string",
    "merge_package_source_specs",
    "normalize_package_source_for_settings",
    "parse_package_source_spec",
    "parse_source",
    "resolve_package_source_from_settings",
]
