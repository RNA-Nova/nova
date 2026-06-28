"""
Agent loop facade: EventStream-based entry points.

This module provides the public stream-facing wrappers around the core
async loop implementation in `loop.py`. It is not the API itself;
`nova_agent.agent_loop.__init__` re-exports these symbols.
"""

import asyncio
from typing import List, Optional

from nova_ai import EventStream

from ..signal import AbortSignal
from ..types import (
    AgentContext,
    AgentEvent,
    AgentEventSink,
    AgentLoopConfig,
    AgentMessage,
    StreamFn,
)

from .loop import run_agent_loop, run_agent_loop_continue


class AgentEventStream(EventStream[AgentEvent, List[AgentMessage]]):
    """An asynchronous stream of AgentEvents."""

    def __init__(self):
        def is_complete(event: AgentEvent) -> bool:
            return getattr(event, "type", None) == "agent_end"

        def extract_result(event: AgentEvent) -> List[AgentMessage]:
            return getattr(event, "messages", []) or []

        super().__init__(is_complete, extract_result)


def _stream_sink(stream: AgentEventStream) -> AgentEventSink:
    async def sink(event: AgentEvent) -> None:
        stream.push(event)

    return sink


def agent_loop(
    prompts: List[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[StreamFn] = None,
) -> AgentEventStream:
    """
    Start an agent loop with a new prompt message.
    The prompt is added to the context and events are emitted for it.
    """
    stream = AgentEventStream()

    async def _run():
        await run_agent_loop(
            prompts,
            context,
            config,
            _stream_sink(stream),
            signal,
            stream_fn,
        )

    asyncio.create_task(_run())
    return stream


def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[StreamFn] = None,
) -> AgentEventStream:
    """
    Continue an agent loop from the current context without adding a new message.
    Used for retries – context already has user message or tool results.
    """
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")

    if context.messages[-1].role == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    stream = AgentEventStream()

    async def _run():
        await run_agent_loop_continue(
            context,
            config,
            _stream_sink(stream),
            signal,
            stream_fn,
        )

    asyncio.create_task(_run())
    return stream
