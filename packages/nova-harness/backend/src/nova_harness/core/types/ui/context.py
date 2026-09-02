"""UI 能力抽象上下文接口（泛型 transport，零词汇）。

只定义**传输语义**，不定义任何交互词汇：所有 method（select/confirm/
input/notify/form/自定义原语）都是自由字符串，其 params/result 契约归
包作者（官方 bundle 定义标准词汇，第三方包可自定义）。

设计见 ``packages/nova-harness/frontend/docs/ui-primitives.md``：

- ``request`` / ``notify`` 是反向通道的完备最小集——时序二分（要应答 /
  不要应答）即消息交互的完备分类；内容全部下沉到 ``method + params``；
- 能力检查前置：前端未宣告的 method 根本不发帧（NoOp 即全降级）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Set

from nova_harness.core.types.ui.primitives import UIResponse


class UIContext(ABC):
    """前端 UI 反向原语抽象接口（泛型 transport）。

    实现者（Transport / NoOp）只需实现 ``capabilities``、``request``、
    ``notify`` 三个抽象成员。便捷方法（select/confirm/input 等）不在此
    定义——它们是词汇层，归包（官方 bundle 的 ``ui_primitives`` 糖库）。
    """

    @property
    @abstractmethod
    def capabilities(self) -> Set[str]:
        """返回前端宣告支持的 UI method 集合。"""

    def has_capability(self, method: str) -> bool:
        """检查前端是否宣告支持指定 method。"""
        return method in self.capabilities

    @abstractmethod
    async def request(
        self, method: str, params: Dict[str, Any], signal: Any = None
    ) -> UIResponse:
        """发送一个需要响应的 UI request。

        ``signal``（可选）为调用方 abort 信号：abort 时实现侧负责撤销
        前端对话框（``ui/cancel``）并按 cancelled 解决。
        """

    @abstractmethod
    def notify(self, method: str, params: Dict[str, Any]) -> None:
        """发送一个不需要响应的 UI 通知。"""


__all__ = ["UIContext"]
