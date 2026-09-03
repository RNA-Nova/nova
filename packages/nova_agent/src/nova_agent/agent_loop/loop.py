"""
Agent loop core: main loop and assistant streaming.
"""

import dataclasses
from typing import List, Optional

from nova_ai import (
    AbortSignal,
    AssistantMessage,
    Context,
    to_thinking_level,
)

from ..stream_fn import builtin_fallback_stream_fn, get_default_stream_fn
from ..types import (
    AgentContext,
    AgentEndEvent,
    AgentEventSink,
    AgentLoopConfig,
    AgentMessage,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    PrepareNextTurnContext,
    ShouldStopAfterTurnContext,
    StreamFn,
    TurnEndEvent,
    TurnStartEvent,
)
from ..utils import default_convert_to_llm, invoke_hook
from .tools import execute_tool_calls, fail_tool_calls_from_truncated_message


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
    turn_index = 0
    pending_messages = await invoke_hook(config.get_steering_messages, default=[]) or []

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
                # "length" 停止意味着输出被 token 上限截断，该消息里每个 tool call
                # 的参数都可能被截断。全部 fail，而不是执行可能残缺的调用。
                if assistant_msg.stop_reason == "length":
                    executed_batch = await fail_tool_calls_from_truncated_message(
                        tool_calls, emit
                    )
                else:
                    executed_batch = await execute_tool_calls(
                        current_context,
                        assistant_msg,
                        tool_calls,
                        config,
                        signal,
                        emit,
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
                turn_index=turn_index,
            )
            next_turn_snapshot = await invoke_hook(
                config.prepare_next_turn, next_turn_context
            )
            if next_turn_snapshot:
                if next_turn_snapshot.context is not None:
                    current_context = next_turn_snapshot.context
                stream_options = config.stream_options
                model = config.model
                if next_turn_snapshot.model is not None:
                    model = next_turn_snapshot.model
                if next_turn_snapshot.thinking_level is not None:
                    # 状态侧级别 → 请求侧：OFF 时 reasoning=None（不发送）
                    stream_options = dataclasses.replace(
                        stream_options,
                        reasoning=to_thinking_level(next_turn_snapshot.thinking_level),
                    )
                config = dataclasses.replace(
                    config, model=model, stream_options=stream_options
                )

            # Graceful stop hook
            should_stop = bool(
                await invoke_hook(
                    config.should_stop_after_turn,
                    ShouldStopAfterTurnContext(
                        message=assistant_msg,
                        tool_results=tool_results,
                        context=current_context,
                        new_messages=new_messages,
                        turn_index=turn_index,
                    ),
                    default=False,
                )
            )
            if should_stop:
                await emit(AgentEndEvent(messages=new_messages))
                return

            turn_index += 1
            pending_messages = (
                await invoke_hook(config.get_steering_messages, default=[]) or []
            )

        # Agent would stop here. Check for follow-up messages.
        follow_up = await invoke_hook(config.get_follow_up_messages, default=[]) or []
        if follow_up:
            pending_messages = follow_up
            continue

        # No more messages, exit
        break

    await emit(AgentEndEvent(messages=new_messages))


# ----------------------------------------------------------------------
# Assistant response streaming
# ----------------------------------------------------------------------


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
    messages = await invoke_hook(
        config.transform_context, context.messages, signal, default=context.messages
    )

    # Convert to LLM-compatible messages (AgentMessage[] → Message[])
    convert = config.convert_to_llm or default_convert_to_llm
    llm_messages = await invoke_hook(convert, messages)

    # Build LLM context
    llm_context = Context(
        system_prompt=context.system_prompt,
        messages=llm_messages,
        tools=context.tools or [],
    )

    stream_func = stream_fn or get_default_stream_fn() or builtin_fallback_stream_fn()

    # Resolve API key (important for expiring tokens)
    resolved_api_key = config.stream_options.api_key
    resolved = await invoke_hook(config.get_api_key, config.model.provider)
    if resolved:
        resolved_api_key = resolved

    # 不修改调用方传入的 stream_options，生成拷贝并注入本次调用的 api_key / signal
    stream_options = dataclasses.replace(
        config.stream_options, api_key=resolved_api_key, signal=signal
    )

    # Call the underlying streaming function (returns async iterator of events)
    response = await invoke_hook(
        stream_func,
        config.model,
        llm_context,
        stream_options,
    )

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
