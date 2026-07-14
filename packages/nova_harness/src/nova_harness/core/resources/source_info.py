"""资源来源信息辅助函数。

- 把 ``ResolvedResource.metadata`` / 扩展贡献路径元数据转成 ``SourceInfo``。
- 按路径精确匹配或前缀匹配查找资源对应的 ``SourceInfo``。
"""

from pathlib import Path
from typing import List, Literal, Optional, cast

from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.package_manager import ResolvedResource
from nova_harness.core.types.resources.paths import ResourceExtensionPathEntry


def _maybe_enum_value(value):
    """把枚举成员转为其 value；已是普通值时直接返回。"""
    if hasattr(value, "value"):
        return value.value
    return value


def source_info_from_metadata(resource: ResolvedResource) -> SourceInfo:
    """根据 ``ResolvedResource`` 的 ``PathMetadata`` 构造 ``SourceInfo``。

    保持与 ``resources/loaders/extensions.py`` 中 ``_source_info_from_metadata``
    相同的字段映射。
    """
    metadata = resource.metadata
    scope = cast(Literal["user", "project", "temporary"], _maybe_enum_value(metadata.scope))
    origin = cast(
        Literal["package", "top-level", "local", "auto"],
        _maybe_enum_value(metadata.origin),
    )
    return SourceInfo(
        path=resource.path,
        source=metadata.source,
        scope=scope,
        origin=origin,
        base_dir=metadata.base_dir,
    )


def source_info_from_extension_entry(
    entry: ResourceExtensionPathEntry,
) -> SourceInfo:
    """把扩展通过 ``resources_discover`` 贡献的路径项转成 ``SourceInfo``。"""
    metadata = entry.metadata
    return SourceInfo(
        path=entry.path,
        source=metadata.source,
        scope=metadata.scope,
        origin=metadata.origin,
        base_dir=metadata.base_dir,
    )


def find_source_info_for_path(
    resource_path: Optional[str], source_infos: List[SourceInfo]
) -> Optional[SourceInfo]:
    """在 ``source_infos`` 中查找 ``resource_path`` 对应的 ``SourceInfo``。

    匹配规则：
    1. 精确匹配 resolved path。
    2. 前缀匹配：``resource_path`` 位于某个 ``source_info.path`` 目录下。
    3. 未找到返回 None。
    """
    if not resource_path:
        return None

    try:
        normalized_resource = Path(resource_path).resolve()
    except (OSError, ValueError):
        return None

    # 精确匹配
    for source_info in source_infos:
        try:
            normalized_source = Path(source_info.path).resolve()
        except (OSError, ValueError):
            continue
        if normalized_resource == normalized_source:
            # 返回一份 path 指向实际资源路径的副本
            return SourceInfo(
                path=resource_path,
                source=source_info.source,
                scope=source_info.scope,
                origin=source_info.origin,
                base_dir=source_info.base_dir,
            )

    # 前缀匹配
    for source_info in source_infos:
        try:
            normalized_source = Path(source_info.path).resolve()
        except (OSError, ValueError):
            continue
        try:
            normalized_resource.relative_to(normalized_source)
        except ValueError:
            continue
        return SourceInfo(
            path=resource_path,
            source=source_info.source,
            scope=source_info.scope,
            origin=source_info.origin,
            base_dir=source_info.base_dir,
        )

    return None


__all__ = [
    "source_info_from_metadata",
    "source_info_from_extension_entry",
    "find_source_info_for_path",
]
