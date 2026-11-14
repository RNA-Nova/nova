"""Project Trust 决议回调接口。"""

from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from nova_harness.extensions.loading import LoadedExtensionsResult


ResolveProjectTrustCallback = Callable[["LoadedExtensionsResult"], Awaitable[bool]]
"""接收预加载扩展结果，返回是否信任当前项目。"""


__all__ = ["ResolveProjectTrustCallback"]
