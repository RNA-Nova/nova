"""包安装与运行时资源解析接口。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional, Protocol

if TYPE_CHECKING:
    from nova_harness.package.models import PackageMetadata
    from nova_harness.package.resolution import ResolvedPaths


class PackageOperationsProtocol(Protocol):
    """包管理器安装/列表能力，供 ``PackageResolver`` 缺失包时回调。"""

    def install(self, source: str) -> Any: ...
    def list(self) -> List[PackageMetadata]: ...


class PackageManagerProtocol(PackageOperationsProtocol, Protocol):
    """统一包管理器能力：安装/列表与运行时资源解析。"""

    async def resolve_resources(
        self,
        *,
        install_missing_packages: Optional[bool] = None,
    ) -> ResolvedPaths: ...


__all__ = ["PackageManagerProtocol", "PackageOperationsProtocol"]
