"""
Tool execution logic for the agent loop.
"""

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, List, Optional, Union

from nova_ai import AbortSignal, AssistantMessage, TextContent, ToolResultMessage

from ..types import (
    AfterToolCallContext,
    AgentContext,
    AgentEventSink,
    AgentLoopConfig,
    AgentToolCall,
    AgentToolResult,
    BeforeToolCallContext,
    ExecutedToolCallBatch,
    ExecutedToolCallOutcome,
    FinalizedToolCallOutcome,
    MessageEndEvent,
    MessageStartEvent,
    PreparedToolCall,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from ..types.tool_execution import (
    _ImmediateToolCallOutcome,
    _PreparedToolCallModel,
)
from ..utils import invoke_hook, validate_tool_arguments
from .execution_gate import ToolExecutionGate

logger = logging.getLogger(__name__)


async def execute_tool_calls(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: List[AgentToolCall],
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
) -> ExecutedToolCallBatch:
    """Execute tool calls from an assistant message.

    调度语义（codex parallel.rs 对位）：
    - ``config.tool_execution == "sequential"``：全批严格串行（显式配置优先）；
    - 其余走门控并行路径：parallel 工具共享读门重叠执行，sequential
      工具取写门等全场排空后独占——批内含 sequential 不再毒化整批为串行。
    """
    if config.tool_execution == "sequential":
        return await _execute_tool_calls_sequential(
            current_context, assistant_message, tool_calls, config, signal, emit
        )
    return await _execute_tool_calls_parallel(
        current_context, assistant_message, tool_calls, config, signal, emit
    )


async def fail_tool_calls_from_truncated_message(
    tool_calls: List[AgentToolCall],
    emit: AgentEventSink,
) -> ExecutedToolCallBatch:
    """Fail all tool calls from an assistant message truncated by the token limit.

    流式 tool-call 参数由 salvage JSON 解析收尾，被 ``length`` 截断的消息可能产生
    "能解析但悄悄不完整"的参数。这样的调用执行起来不安全：全部标记为错误，
    让模型在下一轮重新发起完整调用。
    """
    messages: List[ToolResultMessage] = []
    for tool_call in tool_calls:
        await emit(
            ToolExecutionStartEvent(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                args=tool_call.arguments,
            )
        )
        finalized = FinalizedToolCallOutcome(
            tool_call=tool_call,
            result=_create_error_tool_result(
                f'Tool call "{tool_call.name}" was not executed: the response hit the '
                "output token limit, so its arguments may be truncated. Re-issue the "
                "tool call with complete arguments."
            ),
            is_error=True,
        )
        await _emit_tool_execution_end(finalized, emit)
        tool_result_message = _create_tool_result_message(finalized)
        await _emit_tool_result_message(tool_result_message, emit)
        messages.append(tool_result_message)
    return ExecutedToolCallBatch(messages=messages, terminate=False)


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
    """门控并行：prepare 串行（提交序），执行期按工具声明过门。

    parallel 工具共享读门重叠执行；sequential 工具取写门等全场排空
    后独占（codex ``parallel.rs`` 的公平 RwLock 对位——读写门按批
    作用域，批完即销，无跨批状态）。
    """
    gate = ToolExecutionGate()
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

        def _make_executor(
            prep: _PreparedToolCallModel,
        ) -> Callable[[], Awaitable[FinalizedToolCallOutcome]]:
            # sequential 声明的工具取写门（独占），其余读门（重叠）
            write = prep.tool.execution_mode == "sequential"

            async def executor() -> FinalizedToolCallOutcome:
                admitted = await _acquire_gate_or_aborted(gate, write, signal)
                if not admitted:
                    # 等门期间被 abort：不起跑，直接产出 aborted 结果
                    finalized = FinalizedToolCallOutcome(
                        tool_call=prep.tool_call,
                        result=_create_error_tool_result("Operation aborted"),
                        is_error=True,
                    )
                    await _emit_tool_execution_end(finalized, emit)
                    return finalized
                try:
                    executed = await _execute_prepared_tool_call(prep, signal, emit)
                    finalized = await _finalize_executed_tool_call(
                        current_context,
                        assistant_message,
                        prep,
                        executed,
                        config,
                        signal,
                    )
                    await _emit_tool_execution_end(finalized, emit)
                    return finalized
                finally:
                    await gate.release(write)

            return executor

        finalized_entries.append(_make_executor(preparation))
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


async def _acquire_gate_or_aborted(
    gate: ToolExecutionGate,
    write: bool,
    signal: Optional[AbortSignal],
) -> bool:
    """等门或等 abort，先到先赢。

    返回 ``True`` = 已持门（调用方负责 release）；``False`` = 等门期间
    被 abort——门的取消安全逻辑已自摘，调用方不得起跑、无需 release。
    """
    if signal is not None and signal.aborted:
        return False
    if signal is None:
        await gate.acquire(write)
        return True

    acquire_task = asyncio.create_task(gate.acquire(write))
    abort_task = asyncio.create_task(signal.wait())
    try:
        done, _pending = await asyncio.wait(
            {acquire_task, abort_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if acquire_task in done:
            abort_task.cancel()
            try:
                await abort_task
            except (asyncio.CancelledError, Exception):
                pass
            await acquire_task  # 传播门的异常（正常路径：已持门）
            return True
        # abort 先到：取消等门（acquire 的取消安全逻辑自摘），不起跑
        acquire_task.cancel()
        try:
            await acquire_task
        except asyncio.CancelledError:
            pass
        return False
    finally:
        if not abort_task.done():
            abort_task.cancel()


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
        prepared_args = tool.prepare_arguments(tool_call.arguments)
        prepared_tool_call = (
            tool_call.model_copy(update={"arguments": prepared_args})
            if prepared_args is not tool_call.arguments
            else tool_call
        )

        validated_args = validate_tool_arguments(tool, prepared_tool_call)

        before_result = await invoke_hook(
            config.before_tool_call,
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
            if before_result.terminate:
                # 拦截 + 终止：错误结果带 terminate=True，经批终止判定收口
                result = result.model_copy(update={"terminate": True})
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
    """Execute a prepared tool call and collect updates.

    ``on_update`` 允许被工具从**任意线程**调用（例如执行器用
    ``asyncio.to_thread`` 包装阻塞库时，工作线程里直接回调进度）：

    - 在事件循环线程上调用（常规路径）：直接 ``create_task``，延迟一跳；
    - 从其他线程调用：``loop.call_soon_threadsafe`` 编组回事件循环，
      避免 ``asyncio.create_task`` 在非循环线程上抛 RuntimeError。

    ``execute`` 返回后先 ``sleep(0)`` 让排队的调度回调全部落进
    ``update_tasks``，再统一等待发射完成，保证 ``tool_execution_end``
    一定排在所有 ``tool_execution_update`` 之后。
    """
    loop = asyncio.get_running_loop()
    update_tasks: List[asyncio.Task] = []
    accepting_updates = True

    def on_update(partial_result: AgentToolResult[Any]) -> None:
        if not accepting_updates:
            return

        def _schedule() -> None:
            try:
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
            except Exception:
                # 进度上报永远不能影响工具执行：畸形 partial_result
                # 或调度失败只记日志，不向工具的回调路径抛错。
                logger.warning(
                    "Failed to schedule tool execution update for %s",
                    prepared.tool_call.name,
                    exc_info=True,
                )

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _schedule()
            return
        try:
            loop.call_soon_threadsafe(_schedule)
        except RuntimeError:
            # 事件循环已关闭（teardown 竞态）：丢弃该次更新。
            pass

    async def _drain_updates() -> None:
        # 让 call_soon_threadsafe 排入的调度回调先跑完，update_tasks 才完整
        await asyncio.sleep(0)
        if update_tasks:
            await asyncio.gather(*update_tasks, return_exceptions=True)

    try:
        result = await prepared.tool.execute(
            prepared.tool_call.id,
            prepared.args,
            signal,
            on_update,
        )
        accepting_updates = False
        await _drain_updates()
        return ExecutedToolCallOutcome(result=result, is_error=False)
    except Exception as e:
        accepting_updates = False
        await _drain_updates()
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
    # 结果级错误标记（工具预期内失败，pi 对齐）与执行级错误（异常路径）合并；
    # afterToolCall 钩子仍拥有最终覆盖权
    is_error = executed.is_error or getattr(result, "is_error", False)

    if config.after_tool_call:
        try:
            after_result = await invoke_hook(
                config.after_tool_call,
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
                # model_copy 保留 added_tool_names 等 afterToolCall 不可覆盖的字段
                result = result.model_copy(
                    update={
                        "content": (
                            after_result.content
                            if after_result.content is not None
                            else result.content
                        ),
                        "details": (
                            after_result.details
                            if after_result.details is not None
                            else result.details
                        ),
                        "terminate": (
                            after_result.terminate
                            if after_result.terminate is not None
                            else result.terminate
                        ),
                    }
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


def _should_terminate_tool_batch(
    finalized_calls: List[FinalizedToolCallOutcome],
) -> bool:
    return len(finalized_calls) > 0 and all(
        finalized.result.terminate is True for finalized in finalized_calls
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
        added_tool_names=(
            finalized.result.added_tool_names
            if finalized.result.added_tool_names
            else None
        ),
        is_error=finalized.is_error,
        timestamp=int(time.time() * 1000),
    )


async def _emit_tool_result_message(
    tool_result_message: ToolResultMessage,
    emit: AgentEventSink,
) -> None:
    await emit(MessageStartEvent(message=tool_result_message))
    await emit(MessageEndEvent(message=tool_result_message))
