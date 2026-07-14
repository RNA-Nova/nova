"""无 UI 时的空 UI 上下文实现。"""

from typing import Any, Dict, Set

from nova_harness.core.types.ui.context import UIContext
from nova_harness.core.types.ui.primitives import UIResponse


class NoOpUIContext(UIContext):
    """无 UI 时的空实现，所有 request 安全降级。"""

    @property
    def capabilities(self) -> Set[str]:
        return set()

    async def request(self, method: str, params: Dict[str, Any]) -> UIResponse:
        return UIResponse()

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        pass


__all__ = ["NoOpUIContext"]
