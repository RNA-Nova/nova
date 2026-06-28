"""
Tool execution logic for the agent loop.
"""

import asyncio
from typing import Any, Awaitable, Callable, List, Optional, Union

from nova_ai import AssistantMessage, TextContent, ToolResultMessage

from ..signal import AbortSignal
from ..utils import validate_tool_arguments
from ..types import (
    AgentContext,
    AgentEventSink,
    AgentLoopConfig,
    AgentTool,
    AgentToolCall,
    AgentToolResult,
    AfterToolCallContext,
    AfterToolCallResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    ExecutedToolCallBatch,
    ExecutedToolCallOutcome,
    FinalizedToolCallOutcome,
    MessageEndEvent,
    MessageStartEvent,
    PreparedToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionMode,
)
from ..types.tool_execution import (
    _ImmediateToolCallOutcome,
    _PreparedToolCallModel,
)


async def execute_tool_calls(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: List[AgentToolCall],
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
) -> ExecutedToolCallBatch:
    """Execute tool calls from an assistant message."""
    has_sequential_tool_call = any(
        _tool_execution_mode(current_context.tools, tc) == "sequential"
        for tc in tool_calls
    )
    if config.tool_execution == "sequential" or has_sequential_tool_call:
        return await _execute_tool_calls_sequential(
            current_context, assistant_message, tool_calls, config, signal, emit
        )
    return await _execute_tool_calls_parallel(
        current_context, assistant_message, tool_calls, config, signal, emit
    )


def _tool_execution_mode(
    tools: Optional[List[AgentTool]], tool_call: AgentToolCall
) -> ToolExecutionMode:
    tool = next((t for t in (tools or []) if t.name == tool_call.name), None)
    if tool and getattr(tool, "execution_mode", None):
        return tool.execution_mode
    return "parallel"


async def _execute_tool_calls_sequential(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: List[AgentToolCall],
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
) -> ExecutedToolCallBatch:
    """Execute tool calls one by one."""
    finalized_calls: List[FinalizedToolCallOutcome] = []
    messages: List[ToolResultMessage] = []

    for tool_call in tool_calls:
        await emit(
            ToolExecutionStartEvent(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                args=tool_call.arguments,
            )
        )

        preparation = await _prepare_tool_call(
            current_context, assistant_message, tool_call, config, signal
        )
        if preparation.kind == "immediate":
            finalized = FinalizedToolCallOutcome(
                tool_call=tool_call,
                result=preparation.result,
                is_error=preparation.is_error,
            )
        else:
            executed = await _execute_prepared_tool_call(preparation, signal, emit)
            finalized = await _finalize_executed_tool_call(
                current_context,
                assistant_message,
                preparation,
                executed,
                config,
                signal,
            )

        await _emit_tool_execution_end(finalized, emit)
        tool_result_message = _create_tool_result_message(finalized)
        await _emit_tool_result_message(tool_result_message, emit)
        finalized_calls.append(finalized)
        messages.append(tool_result_message)

        if signal and signal.aborted:
            break

    return ExecutedToolCallBatch(
        messages=messages,
        terminate=_should_terminate_tool_batch(finalized_calls),
    )


async def _execute_tool_calls_parallel(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: List[AgentToolCall],
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
) -> ExecutedToolCallBatch:
    """Prepare tool calls sequentially, then execute allowed tools concurrently."""
    finalized_entries: List[
        Union[
            FinalizedToolCallOutcome, Callable[[], Awaitable[FinalizedToolCallOutcome]]
        ]
    ] = []

    for tool_call in tool_calls:
        await emit(
            ToolExecutionStartEvent(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                args=tool_call.arguments,
            )
        )

        preparation = await _prepare_tool_call(
            current_context, assistant_message, tool_call, config, signal
        )
        if preparation.kind == "immediate":
            finalized = FinalizedToolCallOutcome(
                tool_call=tool_call,
                result=preparation.result,
                is_error=preparation.is_error,
            )
            await _emit_tool_execution_end(finalized, emit)
            finalized_entries.append(finalized)
            if signal and signal.aborted:
                break
            continue

        async def make_executor(
            prep: _PreparedToolCallModel,
        ) -> Callable[[], Awaitable[FinalizedToolCallOutcome]]:
            async def executor() -> FinalizedToolCallOutcome:
                executed = await _execute_prepared_tool_call(prep, signal, emit)
                finalized = await _finalize_executed_tool_call(
                    current_context, assistant_message, prep, executed, config, signal
                )
                await _emit_tool_execution_end(finalized, emit)
                return finalized

            return executor

        finalized_entries.append(await make_executor(preparation))
        if signal and signal.aborted:
            break

    ordered_finalized = await asyncio.gather(
        *[
            entry() if callable(entry) else _async_resolve(entry)
            for entry in finalized_entries
        ]
    )

    messages: List[ToolResultMessage] = []
    for finalized in ordered_finalized:
        tool_result_message = _create_tool_result_message(finalized)
        await _emit_tool_result_message(tool_result_message, emit)
        messages.append(tool_result_message)

    return ExecutedToolCallBatch(
        messages=messages,
        terminate=_should_terminate_tool_batch(ordered_finalized),
    )


async def _async_resolve(value: FinalizedToolCallOutcome) -> FinalizedToolCallOutcome:
    return value


async def _prepare_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_call: AgentToolCall,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
) -> PreparedToolCall:
    """Prepare a tool call: find tool, validate args, run beforeToolCall hook."""
    tool = next(
        (t for t in (current_context.tools or []) if t.name == tool_call.name), None
    )
    if not tool:
        result = _create_error_tool_result(f"Tool {tool_call.name} not found")
        return _ImmediateToolCallOutcome(result=result, is_error=True)

    try:
        prepared_args = (
            tool.prepare_arguments(tool_call.arguments)
            if hasattr(tool, "prepare_arguments")
            else tool_call.arguments
        )
        prepared_tool_call = (
            tool_call.model_copy(update={"arguments": prepared_args})
            if prepared_args is not tool_call.arguments
            else tool_call
        )

        validated_args = validate_tool_arguments(tool, prepared_tool_call)

        before_result = await _maybe_call_before_tool_call(
            config,
            BeforeToolCallContext(
                assistant_message=assistant_message,
                tool_call=tool_call,
                args=validated_args,
                context=current_context,
            ),
            signal,
        )
        if signal and signal.aborted:
            result = _create_error_tool_result("Operation aborted")
            return _ImmediateToolCallOutcome(result=result, is_error=True)
        if before_result and before_result.block:
            result = _create_error_tool_result(
                before_result.reason or "Tool execution was blocked"
            )
            return _ImmediateToolCallOutcome(result=result, is_error=True)
        if signal and signal.aborted:
            result = _create_error_tool_result("Operation aborted")
            return _ImmediateToolCallOutcome(result=result, is_error=True)

        return _PreparedToolCallModel(
            tool_call=tool_call,
            tool=tool,
            args=validated_args,
        )
    except Exception as e:
        return _ImmediateToolCallOutcome(
            result=_create_error_tool_result(str(e)),
            is_error=True,
        )


async def _execute_prepared_tool_call(
    prepared: _PreparedToolCallModel,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
) -> ExecutedToolCallOutcome:
    """Execute a prepared tool call and collect updates."""
    update_tasks: List[asyncio.Task] = []
    accepting_updates = True

    def on_update(partial_result: AgentToolResult[Any]) -> None:
        if not accepting_updates:
            return
        update_tasks.append(
            asyncio.create_task(
                emit(
                    ToolExecutionUpdateEvent(
                        tool_call_id=prepared.tool_call.id,
                        tool_name=prepared.tool_call.name,
                        args=prepared.tool_call.arguments,
                        partial_result=partial_result,
                    )
                )
            )
        )

    try:
        result = await prepared.tool.execute(
            prepared.tool_call.id,
            prepared.args,
            signal,
            on_update,
        )
        accepting_updates = False
        if update_tasks:
            await asyncio.gather(*update_tasks, return_exceptions=True)
        return ExecutedToolCallOutcome(result=result, is_error=False)
    except Exception as e:
        accepting_updates = False
        if update_tasks:
            await asyncio.gather(*update_tasks, return_exceptions=True)
        return ExecutedToolCallOutcome(
            result=_create_error_tool_result(str(e)),
            is_error=True,
        )
    finally:
        accepting_updates = False


async def _finalize_executed_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    prepared: _PreparedToolCallModel,
    executed: ExecutedToolCallOutcome,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
) -> FinalizedToolCallOutcome:
    """Run afterToolCall hook and merge overrides."""
    result = executed.result
    is_error = executed.is_error

    if config.after_tool_call:
        try:
            after_result = await _maybe_call_after_tool_call(
                config,
                AfterToolCallContext(
                    assistant_message=assistant_message,
                    tool_call=prepared.tool_call,
                    args=prepared.args,
                    result=result,
                    is_error=is_error,
                    context=current_context,
                ),
                signal,
            )
            if after_result:
                result = AgentToolResult(
                    content=(
                        after_result.content
                        if after_result.content is not None
                        else result.content
                    ),
                    details=(
                        after_result.details
                        if after_result.details is not None
                        else result.details
                    ),
                    terminate=(
                        after_result.terminate
                        if after_result.terminate is not None
                        else result.terminate
                    ),
                )
                is_error = (
                    after_result.is_error
                    if after_result.is_error is not None
                    else is_error
                )
        except Exception as e:
            result = _create_error_tool_result(str(e))
            is_error = True

    return FinalizedToolCallOutcome(
        tool_call=prepared.tool_call,
        result=result,
        is_error=is_error,
    )


async def _maybe_call_before_tool_call(
    config: AgentLoopConfig,
    context: BeforeToolCallContext,
    signal: Optional[AbortSignal],
) -> Optional[BeforeToolCallResult]:
    if not config.before_tool_call:
        return None
    result = config.before_tool_call(context, signal)
    if asyncio.iscoroutine(result):
        return await result
    return result


async def _maybe_call_after_tool_call(
    config: AgentLoopConfig,
    context: AfterToolCallContext,
    signal: Optional[AbortSignal],
) -> Optional[AfterToolCallResult]:
    if not config.after_tool_call:
        return None
    result = config.after_tool_call(context, signal)
    if asyncio.iscoroutine(result):
        return await result
    return result


def _should_terminate_tool_batch(
    finalized_calls: List[FinalizedToolCallOutcome],
) -> bool:
    return len(finalized_calls) > 0 and all(
        getattr(finalized.result, "terminate", None) is True
        for finalized in finalized_calls
    )


def _create_error_tool_result(message: str) -> AgentToolResult[Any]:
    return AgentToolResult(
        content=[TextContent(text=message)],
        details={},
    )


async def _emit_tool_execution_end(
    finalized: FinalizedToolCallOutcome,
    emit: AgentEventSink,
) -> None:
    await emit(
        ToolExecutionEndEvent(
            tool_call_id=finalized.tool_call.id,
            tool_name=finalized.tool_call.name,
            result=finalized.result,
            is_error=finalized.is_error,
        )
    )


def _create_tool_result_message(
    finalized: FinalizedToolCallOutcome,
) -> ToolResultMessage:
    return ToolResultMessage(
        role="toolResult",
        tool_call_id=finalized.tool_call.id,
        tool_name=finalized.tool_call.name,
        content=finalized.result.content,
        details=finalized.result.details,
        is_error=finalized.is_error,
        timestamp=int(asyncio.get_event_loop().time() * 1000),
    )


async def _emit_tool_result_message(
    tool_result_message: ToolResultMessage,
    emit: AgentEventSink,
) -> None:
    await emit(MessageStartEvent(message=tool_result_message))
    await emit(MessageEndEvent(message=tool_result_message))
