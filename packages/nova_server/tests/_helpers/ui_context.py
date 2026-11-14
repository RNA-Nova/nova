"""可配置能力集的 UIContext 测试实现。"""

from typing import Any, Dict, Optional, Set

from nova_harness.ui import UIContext, UIResponse


class FakeUIContext(UIContext):
    """按声明能力集工作的测试 UI；默认接受全部原语。"""

    def __init__(self, capabilities: Optional[Set[str]] = None) -> None:
        self._capabilities = capabilities

    @property
    def capabilities(self) -> Set[str]:
        return set(self._capabilities or ())

    def has_capability(self, method: str) -> bool:
        return self._capabilities is None or method in self._capabilities

    async def request(
        self, method: str, params: Dict[str, Any], *, scope: Optional[str] = None
    ) -> UIResponse:
        return UIResponse()

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        pass
