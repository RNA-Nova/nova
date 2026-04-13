"""
Agent loop implementation in Python.
Converts AgentMessage to LLM messages only at the call boundary.
"""

import asyncio
from typing import (
    AsyncIterator, Optional, List, Callable, Awaitable, Union, Any, cast
)
from .signal import AbortSignal
from dataclasses import dataclass, field
from copy import deepcopy
# Import from nova_ai (replacing pi-ai)
from nova_ai import (
    AssistantMessage,
    Context,
    EventStream,
    stream_simple,
    ToolResultMessage,
    AssistantMessageEvent,  # for type hints
    SimpleStreamOptions,
    TextContent
)

from .utils import validate_tool_arguments

# Import custom agent types (from the previous conversion)
from .events import (
    AgentMessage,
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    StreamFn,
    MessageStartEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    TurnStartEvent,
    TurnEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
    AgentStartEvent,
    AgentEndEvent,
)


# ----------------------------------------------------------------------
# Custom async event stream (replacement for EventStream from pi-ai)
# ----------------------------------------------------------------------


class AgentEventStream(EventStream[AgentEvent, List[AgentMessage]]):
    """
    An asynchronous stream of AgentEvents. Usage:
        stream = AgentEventStream()
        asyncio.create_task(run_loop(..., stream))
        async for event in stream:
            process(event)
    """

    def __init__(self):
        def is_complete(event: AgentEvent) -> bool:
            # 根据 AgentEvent 的实际结构判断是否结束
            # 假设有 done 或 end 类型的事件
            return getattr(event, "type", None) in ("done", "end", "error")

        def extract_result(event: AgentEvent) -> List[AgentMessage]:
            # 从结束事件中提取最终结果
            return getattr(event, "messages", []) or getattr(event, "result", [])

        super().__init__(is_complete, extract_result)

# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

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
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=context.messages + prompts,
        tools=context.tools,
    )
    new_messages: List[AgentMessage] = list(prompts)

    async def _run():
        stream.push(AgentStartEvent())
        stream.push(TurnStartEvent())
        for prompt in prompts:
            stream.push(MessageStartEvent(message=prompt))
            stream.push(MessageEndEvent(message=prompt))

        await _run_loop(current_context, new_messages, config, signal, stream, stream_fn)

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
    current_context = context  # copy? assume caller doesn't modify
    new_messages: List[AgentMessage] = []

    async def _run():
        stream.push(AgentStartEvent())
        stream.push(TurnStartEvent())
        await _run_loop(current_context, new_messages, config, signal, stream, stream_fn)

    asyncio.create_task(_run())
    return stream


# ----------------------------------------------------------------------
# Core loop logic
# ----------------------------------------------------------------------

async def _run_loop(
    current_context: AgentContext,
    new_messages: List[AgentMessage],
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    stream: AgentEventStream,
    stream_fn: Optional[StreamFn],
) -> None:
    """Main loop logic shared by agent_loop and agent_loop_continue."""
    first_turn = True
    # Check for steering messages at start (user may have typed while waiting)
    pending_messages: List[AgentMessage] = []
    if config.get_steering_messages:
        pending_messages = await config.get_steering_messages() or []

    while True:
        has_more_tool_calls = True
        steering_after_tools: Optional[List[AgentMessage]] = None

        while has_more_tool_calls or pending_messages:
            if not first_turn:
                stream.push(TurnStartEvent())
            else:
                first_turn = False

            # Process pending messages (inject before next assistant response)
            if pending_messages:
                for msg in pending_messages:
                    stream.push(MessageStartEvent(message=msg))
                    stream.push(MessageEndEvent(message=msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                pending_messages = []

            # Stream assistant response
            assistant_msg = await _stream_assistant_response(
                current_context, config, signal, stream, stream_fn
            )
            new_messages.append(assistant_msg)

            if assistant_msg.stop_reason in ("error", "aborted"):
                stream.push(TurnEndEvent(message=assistant_msg, tool_results=[]))
                stream.push(AgentEndEvent(messages=new_messages))
                stream.end(new_messages)
                return

            # Check for tool calls
            tool_calls = [c for c in assistant_msg.content if c.type == "toolCall"]
            has_more_tool_calls = len(tool_calls) > 0

            tool_results: List[ToolResultMessage] = []
            if has_more_tool_calls:
                tool_exec = await _execute_tool_calls(
                    current_context.tools,
                    assistant_msg,
                    signal,
                    stream,
                    config.get_steering_messages,
                )
                tool_results = tool_exec.tool_results
                steering_after_tools = tool_exec.steering_messages

                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            stream.push(TurnEndEvent(message=assistant_msg, tool_results=tool_results))

            # Get steering messages after turn completes
            if steering_after_tools:
                pending_messages = steering_after_tools
                steering_after_tools = None
            elif config.get_steering_messages:
                pending_messages = await config.get_steering_messages() or []

        # Agent would stop here. Check for follow-up messages.
        if config.get_follow_up_messages:
            follow_up = await config.get_follow_up_messages() or []
            if follow_up:
                pending_messages = follow_up
                continue

        # No more messages, exit
        break

    stream.push(AgentEndEvent(messages=new_messages))
    stream.end(new_messages)


# ----------------------------------------------------------------------
# Assistant response streaming
# ----------------------------------------------------------------------

async def _stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    stream: AgentEventStream,
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
    llm_messages = await config.convert_to_llm(messages)

    # Build LLM context
    llm_context = Context(
        system_prompt=context.system_prompt,
        messages=llm_messages,
        tools=context.tools,  # type: ignore (tools are AgentTool, but Context expects Tool)
    )

    stream_func = stream_fn or stream_simple

    # Resolve API key (important for expiring tokens)
    api_key = config.api_key
    if config.get_api_key:
        resolved = await config.get_api_key(config.model.provider)
        if resolved:
            api_key = resolved

    # Create a copy of config with resolved api_key
    stream_config = {**config.__dict__, "api_key": api_key}
    # Remove custom fields not needed by stream_simple
    stream_config.pop("convert_to_llm", None)
    stream_config.pop("transform_context", None)
    stream_config.pop("get_api_key", None)
    stream_config.pop("get_steering_messages", None)
    stream_config.pop("get_follow_up_messages", None)
    stream_config.pop("signal", None)
    stream_config.pop("model",None)

    # Call the underlying streaming function (returns async iterator of events)
    response = await stream_func(
        config.model,
        llm_context,
        SimpleStreamOptions(
            **stream_config,
            signal=signal
        ),
    )

    partial_message: Optional[AssistantMessage] = None
    added_partial = False

    async for event in response:
        # event is of type AssistantMessageEvent (from nova_ai)
        if event.type == "start":
            partial_message = event.partial
            context.messages.append(partial_message)
            added_partial = True
            stream.push(MessageStartEvent(message=deepcopy(partial_message)))
        elif event.type in ("text_start", "text_delta", "text_end",
                            "thinking_start", "thinking_delta", "thinking_end",
                            "toolcall_start", "toolcall_delta", "toolcall_end"):
            if partial_message:
                partial_message = event.partial
                context.messages[-1] = partial_message
                stream.push(MessageUpdateEvent(
                    assistant_message_event=event,
                    message=deepcopy(partial_message),
                ))
        elif event.type in ("done", "error"):
            final_message = await response.result()
            if added_partial:
                context.messages[-1] = final_message
            else:
                context.messages.append(final_message)
            if not added_partial:
                stream.push(MessageStartEvent(message=(final_message)))
            stream.push(MessageEndEvent(message=final_message))
            return final_message

    # Fallback (should not happen)
    return await response.result()


# ----------------------------------------------------------------------
# Tool execution
# ----------------------------------------------------------------------

@dataclass
class ToolExecutionResult:
    tool_results: List[ToolResultMessage]
    steering_messages: Optional[List[AgentMessage]] = None


async def _execute_tool_calls(
    tools: Optional[List[AgentTool]],
    assistant_message: AssistantMessage,
    signal: Optional[AbortSignal],
    stream: AgentEventStream,
    get_steering_messages: Optional[Callable[[], Awaitable[List[AgentMessage]]]],
) -> ToolExecutionResult:
    """Execute tool calls from an assistant message."""
    tool_calls = [c for c in assistant_message.content if c.type == "toolCall"]
    results: List[ToolResultMessage] = []
    steering_messages: Optional[List[AgentMessage]] = None

    for idx, tool_call in enumerate(tool_calls):
        tool = next((t for t in (tools or []) if t.name == tool_call.name), None)

        stream.push(ToolExecutionStartEvent(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            args=tool_call.arguments,
        ))

        result: AgentToolResult[Any]
        is_error = False

        try:
            if not tool:
                raise ValueError(f"Tool {tool_call.name} not found")

            # Validate arguments using nova_ai helper
            validated_args = validate_tool_arguments(tool, tool_call)

            # Execute tool (must be async)
            def on_update(partial: AgentToolResult[Any]):
                stream.push(ToolExecutionUpdateEvent(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    args=tool_call.arguments,
                    partial_result=partial,
                ))

            result = await tool.execute(
                tool_call.id,
                validated_args,
                signal,
                on_update,
            )
        except Exception as e:
            result = AgentToolResult(
                content=[
                    TextContent(
                        type="text", 
                        text= str(e)
                    )
                ],
                details={},
            )
            is_error = True

        stream.push(ToolExecutionEndEvent(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            result=result,
            is_error=is_error,
        ))

        tool_result_message = ToolResultMessage(
            role="toolResult",
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            content=result.content,
            details=result.details,
            is_error=is_error,
            timestamp=asyncio.get_event_loop().time(),  # approximate; use int if needed
        )

        results.append(tool_result_message)
        stream.push(MessageStartEvent(message=tool_result_message))
        stream.push(MessageEndEvent(message=tool_result_message))

        # Check for steering messages – skip remaining tools if user interrupted
        if get_steering_messages:
            steering = await get_steering_messages()
            if steering:
                steering_messages = steering
                remaining = tool_calls[idx + 1:]
                for skipped in remaining:
                    results.append(_skip_tool_call(skipped, stream))
                break

    return ToolExecutionResult(tool_results=results, steering_messages=steering_messages)


def _skip_tool_call(
    tool_call: Any,  # tool call object from AssistantMessage content
    stream: AgentEventStream,
) -> ToolResultMessage:
    """Create a 'skipped' tool result for remaining tool calls when interrupted."""
    result = AgentToolResult(
        content=[
            TextContent(
                type="text", 
                text="Skipped due to queued user message."
            )
        ],
        details={},
    )

    stream.push(ToolExecutionStartEvent(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        args=tool_call.arguments,
    ))
    stream.push(ToolExecutionEndEvent(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        result=result,
        is_error=True,
    ))

    tool_result_message = ToolResultMessage(
        role="toolResult",
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        content=result.content,
        details={},
        is_error=True,
        timestamp=asyncio.get_event_loop().time(),
    )

    stream.push(MessageStartEvent(message=tool_result_message))
    stream.push(MessageEndEvent(message=tool_result_message))

    return tool_result_message