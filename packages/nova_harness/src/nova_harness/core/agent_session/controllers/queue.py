"""Steering / follow-up 队列控制。"""

from __future__ import annotations

from typing import Dict, List, Optional

from nova_ai import ImageContent, TextContent, UserMessage

from nova_harness.core.types.events import QueueUpdateEvent
from nova_harness.core.types.protocols import AgentSessionProtocol


class QueueController:
    """封装 AgentSession 的 steering / follow-up 队列管理。"""

    def __init__(self, session: AgentSessionProtocol) -> None:
        self._session = session

    def emit_update(self) -> None:
        """发射队列状态更新事件。"""
        self._session._emit(
            QueueUpdateEvent(
                steering=list(self._session._steering_messages),
                follow_up=list(self._session._follow_up_messages),
            )
        )

    async def steer(
        self, text: str, images: Optional[List[ImageContent]] = None
    ) -> None:
        """在 Agent 运行时插入一条 steering 消息。"""
        self._session._steering_messages.append(text)
        self.emit_update()
        content: List[object] = [TextContent(type="text", text=text)]
        if images:
            content.extend(images)
        self._session.agent.steer(UserMessage(role="user", content=content))

    async def follow_up(
        self, text: str, images: Optional[List[ImageContent]] = None
    ) -> None:
        """在 Agent 完成当前 turn 后追加一条 follow-up 消息。"""
        self._session._follow_up_messages.append(text)
        self.emit_update()
        content: List[object] = [TextContent(type="text", text=text)]
        if images:
            content.extend(images)
        self._session.agent.follow_up(UserMessage(role="user", content=content))

    def clear(self) -> Dict[str, List[str]]:
        """清空所有排队消息并返回之前的内容。"""
        steering = list(self._session._steering_messages)
        follow_up = list(self._session._follow_up_messages)
        self._session._steering_messages = []
        self._session._follow_up_messages = []
        if hasattr(self._session.agent, "clear_all_queues"):
            self._session.agent.clear_all_queues()
        self.emit_update()
        return {"steering": steering, "follow_up": follow_up}

    def get_steering(self) -> List[str]:
        """返回待处理的 steering 消息（只读副本）。"""
        return list(self._session._steering_messages)

    def get_follow_up(self) -> List[str]:
        """返回待处理的 follow-up 消息（只读副本）。"""
        return list(self._session._follow_up_messages)
