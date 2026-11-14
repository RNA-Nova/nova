"""Agent 事件相关的公共回调签名（跨领域事件正典归 ``nova_protocol``）。"""

from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from nova_protocol import AgentEvent

# Agent 循环向外投递 AgentEvent 的回调签名。
AgentEventSink = Callable[["AgentEvent"], Awaitable[None]]

__all__ = ["AgentEventSink"]
