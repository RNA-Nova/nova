"""事件联合类型与监听器类型。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Union

from nova_agent import AgentEvent

from .agent import (
    AfterProviderResponseEvent,
    AgentEndEvent,
    AgentStartEvent,
    BeforeAgentStartEvent,
    BeforeProviderRequestEvent,
    ContextEvent,
    ExtensionErrorEvent,
    InputEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ModelSelectEvent,
    PrepareNextTurnEvent,
    ResourcesDiscoverEvent,
    ShouldStopAfterTurnEvent,
    ThinkingLevelSelectEvent,
    ToolCallEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolResultEvent,
    TurnEndEvent,
    TurnStartEvent,
    UserBashEvent,
)
from .auto import (
    AutoCompactionEndEvent,
    AutoCompactionStartEvent,
    AutoRetryEndEvent,
    AutoRetryStartEvent,
    QueueUpdateEvent,
    SessionInfoChangedEvent,
    ThinkingLevelChangedEvent,
)
from .results import (
    BeforeAgentStartEventResult,
    ContextEventResult,
    InputEventResult,
    MessageEndEventResult,
    PrepareNextTurnEventResult,
    ResourcesDiscoverEventResult,
    SessionBeforeCompactResult,
    SessionBeforeForkResult,
    SessionBeforeSwitchResult,
    SessionBeforeTreeResult,
    ShouldStopAfterTurnEventResult,
    ToolCallEventResult,
    ToolResultEventResult,
    UserBashEventResult,
)
from .session import (
    CompactionEndEvent,
    CompactionStartEvent,
    SessionBeforeCompactEvent,
    SessionBeforeForkEvent,
    SessionBeforeSwitchEvent,
    SessionBeforeTreeEvent,
    SessionCompactEvent,
    SessionShutdownEvent,
    SessionStartEvent,
    SessionTreeEvent,
)

AgentSessionEvent = Union[
    AgentEvent,
    AutoCompactionStartEvent,
    AutoCompactionEndEvent,
    AutoRetryStartEvent,
    AutoRetryEndEvent,
    QueueUpdateEvent,
    SessionInfoChangedEvent,
    ThinkingLevelChangedEvent,
    CompactionStartEvent,
    CompactionEndEvent,
]

AgentSessionEventListener = Union[
    Callable[[AgentSessionEvent], None],
    Callable[[AgentSessionEvent], Awaitable[None]],
]

ExtensionEvent = Union[
    SessionStartEvent,
    SessionShutdownEvent,
    SessionBeforeSwitchEvent,
    SessionBeforeForkEvent,
    SessionBeforeCompactEvent,
    SessionCompactEvent,
    CompactionStartEvent,
    CompactionEndEvent,
    SessionBeforeTreeEvent,
    SessionTreeEvent,
    ContextEvent,
    BeforeProviderRequestEvent,
    AfterProviderResponseEvent,
    BeforeAgentStartEvent,
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    PrepareNextTurnEvent,
    ShouldStopAfterTurnEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserBashEvent,
    InputEvent,
    ModelSelectEvent,
    ThinkingLevelSelectEvent,
    ResourcesDiscoverEvent,
    ExtensionErrorEvent,
]

ExtensionEventResult = Union[
    ContextEventResult,
    ToolCallEventResult,
    ToolResultEventResult,
    MessageEndEventResult,
    BeforeAgentStartEventResult,
    PrepareNextTurnEventResult,
    ShouldStopAfterTurnEventResult,
    SessionBeforeSwitchResult,
    SessionBeforeForkResult,
    SessionBeforeCompactResult,
    SessionBeforeTreeResult,
    UserBashEventResult,
    InputEventResult,
    ResourcesDiscoverEventResult,
    None,
]

ExtensionEventHandler = Callable[
    [Any], Union[ExtensionEventResult, Awaitable[ExtensionEventResult]]
]

__all__ = [
    "AgentSessionEvent",
    "AgentSessionEventListener",
    "ExtensionEvent",
    "ExtensionEventResult",
    "ExtensionEventHandler",
]
