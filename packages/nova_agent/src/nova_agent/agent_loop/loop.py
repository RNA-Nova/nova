"""
Agent loop core: main loop and assistant streaming.
"""

import asyncio
import inspect
from typing import List, Optional

from nova_ai import (
    AssistantMessage,
    Context,
    Message,
    SimpleStreamOptions,
    stream_simple,
)

from ..signal import AbortSignal
from ..types import (
    AgentContext,
    AgentEvent,
    AgentEventSink,
    AgentLoopConfig,
    AgentLoopTurnUpdate,
    AgentMessage,
    PrepareNextTurnContext,
    ShouldStopAfterTurnContext,
    StreamFn,
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
)

from .tools import execute_tool_calls


async def run_agent_loop(
    prompts: List[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[StreamFn] = None,
) -> List[AgentMessage]:
    """Start an agent loop with a new prompt message (async, returns messages)."""
    new_messages: List[AgentMessage] = list(prompts)
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=context.messages + prompts,
        tools=context.tools,
    )

    await emit(AgentStartEvent())
    await emit(TurnStartEvent())
    for prompt in prompts:
        await emit(MessageStartEvent(message=prompt))
        await emit(MessageEndEvent(message=prompt))

    await _run_loop(current_context, new_messages, config, signal, emit, stream_fn)
    return new_messages


async def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[StreamFn] = None,
) -> List[AgentMessage]:
    """Continue an agent loop from the current context (async, returns messages)."""
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")

    if context.messages[-1].role == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    new_messages: List[AgentMessage] = []
    current_context = context

    await emit(AgentStartEvent())
    await emit(TurnStartEvent())

    await _run_loop(current_context, new_messages, config, signal, emit, stream_fn)
    return new_messages


# ----------------------------------------------------------------------
# Core loop logic
# ----------------------------------------------------------------------


async def _run_loop(
    initial_context: AgentContext,
    new_messages: List[AgentMessage],
    initial_config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
    stream_fn: Optional[StreamFn],
) -> None:
    """Main loop logic shared by agent_loop and agent_loop_continue."""
    current_context = initial_context
    config = initial_config
    first_turn = True
    pending_messages: List[AgentMessage] = []
    if config.get_steering_messages:
        pending_messages = await config.get_steering_messages() or []

    while True:
        has_more_tool_calls = True

        while has_more_tool_calls or pending_messages:
            if not first_turn:
                await emit(TurnStartEvent())
            else:
                first_turn = False

            # Process pending messages (inject before next assistant response)
            if pending_messages:
                for msg in pending_messages:
                    await emit(MessageStartEvent(message=msg))
                    await emit(MessageEndEvent(message=msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                pending_messages = []

            # Stream assistant response
            assistant_msg = await _stream_assistant_response(
                current_context, config, signal, emit, stream_fn
            )
            new_messages.append(assistant_msg)

            if assistant_msg.stop_reason in ("error", "aborted"):
                await emit(TurnEndEvent(message=assistant_msg, tool_results=[]))
                await emit(AgentEndEvent(messages=new_messages))
                return

            # Check for tool calls
            tool_calls = [c for c in assistant_msg.content if c.type == "toolCall"]

            tool_results = []
            has_more_tool_calls = False
            if tool_calls:
                executed_batch = await execute_tool_calls(
                    current_context, assistant_msg, tool_calls, config, signal, emit
                )
                tool_results = executed_batch.messages
                has_more_tool_calls = not executed_batch.terminate

                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            await emit(TurnEndEvent(message=assistant_msg, tool_results=tool_results))

            # Allow caller to mutate context/config before next turn
            next_turn_context = PrepareNextTurnContext(
                message=assistant_msg,
                tool_results=tool_results,
                context=current_context,
                new_messages=new_messages,
            )
            next_turn_snapshot = await _maybe_call_prepare_next_turn(
                config, next_turn_context
            )
            if next_turn_snapshot:
                if next_turn_snapshot.context is not None:
                    current_context = next_turn_snapshot.context
                if (
                    next_turn_snapshot.model is not None
                    or next_turn_snapshot.thinking_level is not None
                ):
                    config = config.model_copy(
                        update={
                            "model": (
                                next_turn_snapshot.model
                                if next_turn_snapshot.model is not None
                                else config.model
                            ),
                            "reasoning": (
                                (
                                    None
                                    if next_turn_snapshot.thinking_level == "off"
                                    else next_turn_snapshot.thinking_level
                                )
                                if next_turn_snapshot.thinking_level is not None
                                else config.reasoning
                            ),
                        }
                    )

            # Graceful stop hook
            should_stop = await _maybe_call_should_stop_after_turn(
                config,
                ShouldStopAfterTurnContext(
                    message=assistant_msg,
                    tool_results=tool_results,
                    context=current_context,
                    new_messages=new_messages,
                ),
            )
            if should_stop:
                await emit(AgentEndEvent(messages=new_messages))
                return

            pending_messages = await _maybe_get_steering_messages(config)

        # Agent would stop here. Check for follow-up messages.
        if config.get_follow_up_messages:
            follow_up = await config.get_follow_up_messages() or []
            if follow_up:
                pending_messages = follow_up
                continue

        # No more messages, exit
        break

    await emit(AgentEndEvent(messages=new_messages))


async def _maybe_call_prepare_next_turn(
    config: AgentLoopConfig,
    context: PrepareNextTurnContext,
) -> Optional[AgentLoopTurnUpdate]:
    if not config.prepare_next_turn:
        return None
    result = config.prepare_next_turn(context)
    if asyncio.iscoroutine(result):
        return await result
    return result


async def _maybe_call_should_stop_after_turn(
    config: AgentLoopConfig,
    context: ShouldStopAfterTurnContext,
) -> bool:
    if not config.should_stop_after_turn:
        return False
    result = config.should_stop_after_turn(context)
    if asyncio.iscoroutine(result):
        return await result
    return result


async def _maybe_get_steering_messages(config: AgentLoopConfig) -> List[AgentMessage]:
    if not config.get_steering_messages:
        return []
    return await config.get_steering_messages() or []


# ----------------------------------------------------------------------
# Assistant response streaming
# ----------------------------------------------------------------------


async def _default_convert_to_llm(messages: List[AgentMessage]) -> List[Message]:
    """Default converter: keep only LLM‑compatible messages."""
    return [m for m in messages if m.role in ("user", "assistant", "toolResult")]


async def _stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
    stream_fn: Optional[StreamFn],
) -> AssistantMessage:
    """
    Stream an assistant response from the LLM.
    This is where AgentMessage[] gets transformed to Message[] for the LLM.
    """
    # Apply context transform if configured (AgentMessage[] → AgentMessage[])
    messages = context.messages
    if config.transform_context:
        messages = await config.transform_context(messages, signal)

    # Convert to LLM-compatible messages (AgentMessage[] → Message[])
    convert = config.convert_to_llm or _default_convert_to_llm
    if inspect.iscoroutinefunction(convert):
        llm_messages = await convert(messages)
    else:
        llm_messages = convert(messages)

    # Build LLM context
    llm_context = Context(
        system_prompt=context.system_prompt,
        messages=llm_messages,
        tools=[t for t in (context.tools or [])],
    )

    stream_func = stream_fn or config.stream_fn or stream_simple

    # Resolve API key (important for expiring tokens)
    resolved_api_key = config.api_key
    if config.get_api_key:
        resolved = config.get_api_key(config.model.provider)
        if asyncio.iscoroutine(resolved):
            resolved = await resolved
        if resolved:
            resolved_api_key = resolved

    # Build SimpleStreamOptions from config, excluding agent-specific callbacks
    stream_config = config.model_dump(
        exclude={
            "convert_to_llm",
            "transform_context",
            "get_api_key",
            "get_steering_messages",
            "get_follow_up_messages",
            "should_stop_after_turn",
            "prepare_next_turn",
            "tool_execution",
            "before_tool_call",
            "after_tool_call",
            "model",
        }
    )
    stream_config["api_key"] = resolved_api_key
    stream_config["signal"] = signal
    stream_config["on_payload"] = config.on_payload
    stream_config["on_response"] = config.on_response

    # Call the underlying streaming function (returns async iterator of events)
    response = stream_func(
        config.model,
        llm_context,
        SimpleStreamOptions(**stream_config),
    )
    if asyncio.iscoroutine(response):
        response = await response

    partial_message: Optional[AssistantMessage] = None
    added_partial = False

    async for event in response:
        if event.type == "start":
            partial_message = event.partial
            context.messages.append(partial_message)
            added_partial = True
            await emit(MessageStartEvent(message=partial_message.model_copy()))
        elif event.type in (
            "text_start",
            "text_delta",
            "text_end",
            "thinking_start",
            "thinking_delta",
            "thinking_end",
            "toolcall_start",
            "toolcall_delta",
            "toolcall_end",
        ):
            if partial_message:
                partial_message = event.partial
                context.messages[-1] = partial_message
                await emit(
                    MessageUpdateEvent(
                        assistant_message_event=event,
                        message=partial_message.model_copy(),
                    )
                )
        elif event.type in ("done", "error"):
            final_message = await response.result()
            if added_partial:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
            if not added_partial:
                await emit(MessageStartEvent(message=final_message.model_copy()))
            await emit(MessageEndEvent(message=final_message))
            return final_message

    # Fallback (should not happen)
    final_message = await response.result()
    if added_partial:
        context.messages[-1] = final_message
    else:
        context.messages.append(final_message)
        await emit(MessageStartEvent(message=final_message.model_copy()))
    await emit(MessageEndEvent(message=final_message))
    return final_message
