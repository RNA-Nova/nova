"""UserToolManager — 用户工具注册中心。

只接管"管道"：注册表、目录、invoke 调度。工具的参数与事件对本类
不透明（设计纪律：泛化层不接管"能力"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from nova_agent import CustomAgentMessage

from nova_harness.core.types.resources.user_tools import (
    UserToolDefinition,
    UserToolEventCallback,
    UserToolInfo,
)


@dataclass
class UserToolManager:
    """用户工具（user tool）的统一注册与调用入口。

    框架不内置任何用户工具——所有定义都来自包（经 AgentSession 按当前
    agent 白名单注册），注册路径唯一，没有任何特殊分支。
    """

    _registry: Dict[str, UserToolDefinition] = field(default_factory=dict)

    def register(self, definition: UserToolDefinition) -> None:
        """注册一个用户工具定义（必须携带 execute 执行体）。"""
        if definition.execute is None:
            raise ValueError(f"User tool '{definition.name}' 缺少 execute 执行体")
        self._registry[definition.name] = definition

    def clear(self) -> None:
        """清空注册表（agent 切换/reload 时整体重建）。"""
        self._registry.clear()

    def get(self, name: str) -> Optional[UserToolDefinition]:
        return self._registry.get(name)

    def names(self) -> List[str]:
        return sorted(self._registry)

    def catalog(self) -> List[UserToolInfo]:
        """RPC ``listUserTools`` 的目录形态。"""
        return [
            UserToolInfo(
                name=d.name,
                description=d.description,
                parameters=d.parameters or None,
                source=d.source_info.source if d.source_info else None,
                source_info=d.source_info,
            )
            for d in self._registry.values()
        ]

    async def invoke(
        self,
        name: str,
        params: Optional[Dict[str, Any]] = None,
        on_event: Optional[UserToolEventCallback] = None,
        signal: Any = None,
    ) -> CustomAgentMessage:
        """按名调用一个用户工具，返回产出的消息实例（记录由会话层负责）。"""
        definition = self._registry.get(name)
        if definition is None:
            raise KeyError(
                f"未知的用户工具: '{name}'（可用: {', '.join(self.names())}）"
            )
        assert definition.execute is not None  # register 已校验
        return await definition.execute(params or {}, on_event, signal)


__all__ = ["UserToolManager"]
