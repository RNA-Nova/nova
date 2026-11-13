"""
Tool execution logic for the agent loop.
"""

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, List, Optional, Union

from nova_async import TaskTracker, await_with_signal
from nova_protocol import (
    AbortedError,
    AbortSignal,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    MessageStartEvent,
    TextContent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolResultMessage,
    is_aborted,
)

from ..types import (
    AfterToolCallContext,
    AgentContext,
    AgentEventSink,
    AgentLoopConfig,
    AgentTool,
    AgentToolCall,
    AgentToolCallOutcome,
    BeforeToolCallContext,
    ExecutedToolCallBatch,
    ExecutedToolCallOutcome,
    ImmediateToolCallOutcome,
    PreparedToolCall,
    PreparedToolCallModel,
    RunToolCallOptions,
    ToolCallHooks,
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

    即使严格终态解析标出 ``arguments_error``，被 ``length`` 截断的消息仍需
    整批拒执行并明确要求重发。这样的调用执行起来不安全：全部标记为错误，
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
        finalized = AgentToolCallOutcome(
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
    finalized_calls: List[AgentToolCallOutcome] = []
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
            finalized = AgentToolCallOutcome(
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

        if is_aborted(signal):
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
        Union[AgentToolCallOutcome, Callable[[], Awaitable[AgentToolCallOutcome]]]
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
            finalized = AgentToolCallOutcome(
                tool_call=tool_call,
                result=preparation.result,
                is_error=preparation.is_error,
            )
            await _emit_tool_execution_end(finalized, emit)
            finalized_entries.append(finalized)
            if is_aborted(signal):
                break
            continue

        def _make_executor(
            prep: PreparedToolCallModel,
        ) -> Callable[[], Awaitable[AgentToolCallOutcome]]:
            # sequential 声明的工具取写门（独占），其余读门（重叠）
            write = prep.tool.execution_mode == "sequential"

            async def executor() -> AgentToolCallOutcome:
                admitted = await _acquire_gate_or_aborted(gate, write, signal)
                if not admitted:
                    # 等门期间被 abort：不起跑，直接产出 aborted 结果
                    finalized = AgentToolCallOutcome(
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
                finally:
                    await gate.release(write)
                # after 已在门内产生终态；end/result 通知不再占用执行门，
                # 避免慢监听器饿死后续 sequential 工具。
                await _emit_tool_execution_end(finalized, emit)
                return finalized

            return executor

        finalized_entries.append(_make_executor(preparation))
        if is_aborted(signal):
            break

    # 同一工具批一本账：任何异常都先落闸、取消 sibling、排水后再传播。
    tracker = TaskTracker(name="agent-tool-batch")
    tasks = []
    for entry in finalized_entries:
        task = tracker.spawn(_async_resolve(entry))
        if task is not None:
            tasks.append(task)
    try:
        ordered_finalized = await asyncio.gather(*tasks)
    except BaseException:
        tracker.close()
        tracker.cancel_all()
        try:
            async with asyncio.timeout(0.1):
                await tracker.wait()
        except TimeoutError:
            logger.warning(
                "Tool batch cleanup exceeded 100ms; proceeding with terminal events"
            )
        raise
    tracker.close()
    await tracker.wait()

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

    竞速归 ``nova_async.await_with_signal``（完成优先裁决，取消原语族单点
    ——本函数只做"抛异常 → bool"的翻译，不手写第三份竞速）。返回
    ``True`` = 已持门（调用方负责 release）；``False`` = 等门期间被
    abort——门的取消安全逻辑已自摘，调用方不得起跑、无需 release。
    """
    if signal is None:
        await gate.acquire(write)
        return True
    try:
        await await_with_signal(gate.acquire(write), signal)
    except AbortedError:
        # signal 胜出（含预触发举旗点）：等门协程已被取消落定、门侧自摘
        return False
    return True


async def _async_resolve(
    value: Union[AgentToolCallOutcome, Callable[[], Awaitable[AgentToolCallOutcome]]],
) -> AgentToolCallOutcome:
    if callable(value):
        return await value()
    return value


async def _prepare_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_call: AgentToolCall,
    config: ToolCallHooks,
    signal: Optional[AbortSignal],
    tools: Optional[List[AgentTool[Any, Any]]] = None,
) -> PreparedToolCall:
    """Prepare a tool call: find tool, validate args, run beforeToolCall hook."""
    available_tools = tools if tools is not None else (current_context.tools or [])
    tool = next((t for t in available_tools if t.name == tool_call.name), None)
    if not tool:
        result = _create_error_tool_result(f"Tool {tool_call.name} not found")
        return ImmediateToolCallOutcome(result=result, is_error=True)

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
        if is_aborted(signal):
            result = _create_error_tool_result("Operation aborted")
            return ImmediateToolCallOutcome(result=result, is_error=True)
        if before_result and before_result.block:
            result = _create_error_tool_result(
                before_result.reason or "Tool execution was blocked"
            )
            if before_result.terminate:
                # 拦截 + 终止：错误结果带 terminate=True，经批终止判定收口
                result = result.model_copy(update={"terminate": True})
            return ImmediateToolCallOutcome(result=result, is_error=True)
        if is_aborted(signal):
            result = _create_error_tool_result("Operation aborted")
            return ImmediateToolCallOutcome(result=result, is_error=True)

        return PreparedToolCallModel(
            tool_call=tool_call,
            tool=tool,
            args=validated_args,
        )
    except AbortedError:
        raise
    except Exception as e:
        return ImmediateToolCallOutcome(
            result=_create_error_tool_result(str(e)),
            is_error=True,
        )


async def _execute_prepared_tool_call(
    prepared: PreparedToolCallModel,
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
    update_tasks: List["asyncio.Future[None]"] = []
    accepting_updates = True

    def on_update(partial_result: AgentToolResult[Any]) -> None:
        if not accepting_updates:
            return

        def _schedule() -> None:
            # 工作线程的检查与编组之间存在窗口：循环线程必须再检查一次，
            # 防止 execute/end 后晚到回调创建未管理 Task。
            if not accepting_updates:
                return
            try:
                # ensure_future 接受任意 Awaitable（AgentEventSink 签名不是
                # 严格 Coroutine）；协程输入下行为与 create_task 一致
                update_tasks.append(
                    asyncio.ensure_future(
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

    async def _drain_updates() -> Optional[Exception]:
        nonlocal accepting_updates
        # 先让 call_soon_threadsafe 排入的调度回调全部落账，再关闭接纳；
        # 过早关闭会把 execute 返回前已编组、尚未运行的 update 丢弃。
        await asyncio.sleep(0)
        accepting_updates = False
        if not update_tasks:
            return None
        results = await asyncio.gather(*update_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, AbortedError):
                raise result
            if isinstance(result, Exception):
                return result
        return None

    started_at = time.monotonic()
    try:
        result = await prepared.tool.execute(
            prepared.tool_call.id,
            prepared.args,
            signal,
            on_update,
        )
        # 时长只覆盖 execute 本身；进度排干与 after 钩子不计入。
        duration_ms = round((time.monotonic() - started_at) * 1000)
        update_error = await _drain_updates()
        if update_error is not None:
            return ExecutedToolCallOutcome(
                result=_create_error_tool_result(str(update_error)),
                is_error=True,
                duration_ms=duration_ms,
            )
        return ExecutedToolCallOutcome(
            result=result, is_error=False, duration_ms=duration_ms
        )
    except AbortedError:
        # 用户取消不是工具故障；finally 排干后按原类型直通。
        raise
    except Exception as e:
        duration_ms = round((time.monotonic() - started_at) * 1000)
        await _drain_updates()
        return ExecutedToolCallOutcome(
            result=_create_error_tool_result(str(e)),
            is_error=True,
            duration_ms=duration_ms,
        )
    finally:
        accepting_updates = False
        pending = [task for task in update_tasks if not task.done()]
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


async def _finalize_executed_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    prepared: PreparedToolCallModel,
    executed: ExecutedToolCallOutcome,
    config: ToolCallHooks,
    signal: Optional[AbortSignal],
) -> AgentToolCallOutcome:
    """Run afterToolCall hook and merge overrides."""
    result = executed.result
    # 结果级错误标记（工具预期内失败，pi 对齐）与执行级错误（异常路径）合并；
    # afterToolCall 钩子仍拥有最终覆盖权
    is_error = executed.is_error or result.is_error

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
                # 只换 content 时旧 structuredContent 可能已不再匹配，必须丢弃；
                # 显式给出 structured_content 才替换或重建它。
                structured_content = after_result.structured_content
                if structured_content is None:
                    structured_content = (
                        None
                        if after_result.content is not None
                        else result.structured_content
                    )
                # model_copy 保留 afterToolCall 不可覆盖的其余字段
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
                        "structured_content": structured_content,
                        "usage": (
                            after_result.usage
                            if after_result.usage is not None
                            else result.usage
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
        except AbortedError:
            raise
        except Exception as e:
            result = _create_error_tool_result(str(e))
            is_error = True

    return AgentToolCallOutcome(
        tool_call=prepared.tool_call,
        result=result,
        is_error=is_error,
        duration_ms=executed.duration_ms,
    )


def _should_terminate_tool_batch(
    finalized_calls: List[AgentToolCallOutcome],
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
    finalized: AgentToolCallOutcome,
    emit: AgentEventSink,
) -> None:
    await emit(
        ToolExecutionEndEvent(
            tool_call_id=finalized.tool_call.id,
            tool_name=finalized.tool_call.name,
            result=finalized.result,
            is_error=finalized.is_error,
            duration_ms=finalized.duration_ms,
        )
    )


def _create_tool_result_message(
    finalized: AgentToolCallOutcome,
) -> ToolResultMessage:
    return ToolResultMessage(
        role="toolResult",
        tool_call_id=finalized.tool_call.id,
        tool_name=finalized.tool_call.name,
        content=finalized.result.content,
        details=finalized.result.details,
        usage=finalized.result.usage,
        is_error=finalized.is_error,
        duration_ms=finalized.duration_ms,
        timestamp=int(time.time() * 1000),
    )


async def _emit_tool_result_message(
    tool_result_message: ToolResultMessage,
    emit: AgentEventSink,
) -> None:
    await emit(MessageStartEvent(message=tool_result_message))
    await emit(MessageEndEvent(message=tool_result_message))


async def run_tool_call(
    tool_call: AgentToolCall, options: RunToolCallOptions
) -> AgentToolCallOutcome:
    """复用单次工具调用的校验与钩子，但不发事件、不写转录。

    未知工具、参数错误、拦截与执行异常都以 ``is_error=True`` 结果返回。
    供工具内部的嵌套调用使用，使权限等钩子对子调用同样生效。
    """
    preparation = await _prepare_tool_call(
        options.context,
        options.assistant_message,
        tool_call,
        options,
        options.signal,
        options.tools,
    )
    if preparation.kind == "immediate":
        return AgentToolCallOutcome(
            tool_call=tool_call,
            result=preparation.result,
            is_error=preparation.is_error,
        )

    async def emit(event) -> None:
        if (
            isinstance(event, ToolExecutionUpdateEvent)
            and options.on_update is not None
        ):
            await invoke_hook(options.on_update, event.partial_result)

    executed = await _execute_prepared_tool_call(preparation, options.signal, emit)
    return await _finalize_executed_tool_call(
        options.context,
        options.assistant_message,
        preparation,
        executed,
        options,
        options.signal,
    )
