"""Agent loop：转录差分、请求准备、回合决策与工具调度。"""

import dataclasses
import time
from typing import List, Optional, cast

from nova_ai import (
    ToolStateChanges,
    get_current_tools,
    get_tool_state_changes,
    to_thinking_level,
    to_tool_declaration,
)
from nova_async import AbortSignal
from nova_protocol import (
    AgentEndEvent,
    AgentMessage,
    AgentStartEvent,
    AssistantMessage,
    Message,
    MessageEndEvent,
    MessageStartEvent,
    ModelThinkingLevel,
    StopReason,
    SystemMessage,
    TurnEndEvent,
    TurnStartEvent,
)

from ..types import (
    AgentContext,
    AgentEventSink,
    AgentLoopConfig,
    PrepareNextTurnContext,
    PrepareRequestContext,
    StreamFn,
)
from ..utils import invoke_hook
from .response import stream_assistant_response
from .tools import execute_tool_calls, fail_tool_calls_from_truncated_message


def _with_tool_changes(
    message: SystemMessage, changes: ToolStateChanges
) -> SystemMessage:
    """以最新工具差分替换 system 消息上的旧声明意图。"""
    return message.model_copy(
        update={
            "tools_added": changes.tools_added or None,
            "tools_removed": changes.tools_removed or None,
        }
    )


def _declare_tool_changes(
    context: AgentContext, pending_messages: List[AgentMessage]
) -> List[AgentMessage]:
    """把可执行工具集与转录声明的差异记录成 system 消息。

    若 pending 中已有 system 消息，其工具字段视为声明意图，重写为“已提交
    转录 → 当前可执行集”的真实差分；否则把变更插到第一条非 system 前。
    """
    system_index = next(
        (
            index
            for index in range(len(pending_messages) - 1, -1, -1)
            if pending_messages[index].role == "system"
        ),
        -1,
    )
    pending = (
        cast(SystemMessage, pending_messages[system_index])
        if system_index >= 0
        else None
    )
    baseline = (
        [
            (
                _with_tool_changes(pending, ToolStateChanges([], []))
                if index == system_index
                else message
            )
            for index, message in enumerate(pending_messages)
        ]
        if pending is not None
        else pending_messages
    )
    changes = get_tool_state_changes(
        get_current_tools(cast(List[Message], [*context.messages, *baseline])),
        [to_tool_declaration(tool) for tool in context.tools or []],
    )
    unchanged = not changes.tools_added and not changes.tools_removed
    if pending is not None:
        if unchanged and not pending.tools_added and not pending.tools_removed:
            return pending_messages
        return [
            _with_tool_changes(pending, changes) if index == system_index else message
            for index, message in enumerate(baseline)
        ]
    if unchanged:
        return pending_messages

    update = _with_tool_changes(
        SystemMessage(content="", timestamp=int(time.time() * 1000)), changes
    )
    insert_index = next(
        (
            index
            for index, message in enumerate(pending_messages)
            if message.role != "system"
        ),
        len(pending_messages),
    )
    return [
        *pending_messages[:insert_index],
        update,
        *pending_messages[insert_index:],
    ]


async def run_agent_loop(
    prompts: List[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    signal: Optional[AbortSignal] = None,
    stream_fn: Optional[StreamFn] = None,
) -> List[AgentMessage]:
    """从一组新输入启动循环；输入与工具变更先进入转录。"""
    initial_messages = _declare_tool_changes(context, prompts)
    new_messages: List[AgentMessage] = list(initial_messages)
    current_context = AgentContext(
        messages=[*context.messages, *initial_messages],
        tools=context.tools,
    )

    await emit(AgentStartEvent())
    await emit(TurnStartEvent())
    for prompt in initial_messages:
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
    """从既有转录继续；context 末尾不能是 assistant。"""
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")
    if context.messages[-1].role == "assistant":
        raise ValueError("Cannot continue from message role: assistant")

    new_messages: List[AgentMessage] = []
    await emit(AgentStartEvent())
    await emit(TurnStartEvent())
    await _run_loop(context, new_messages, config, signal, emit, stream_fn)
    return new_messages


async def _run_loop(
    initial_context: AgentContext,
    new_messages: List[AgentMessage],
    initial_config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
    stream_fn: Optional[StreamFn],
) -> None:
    """共享调度器：prepare_next_turn → prepare_request → finish_turn。"""
    current_context = initial_context
    config = initial_config
    last_completed_turn: Optional[PrepareNextTurnContext] = None
    explicit_continuation = False
    first_turn = True
    turn_index = 0
    pending_messages = await invoke_hook(config.get_steering_messages, default=[]) or []

    while True:
        has_more_tool_calls = True
        while has_more_tool_calls or pending_messages:
            prepared_messages: List[AgentMessage] = []
            if last_completed_turn is not None:
                next_turn = await invoke_hook(
                    config.prepare_next_turn, last_completed_turn
                )
                if next_turn is not None:
                    if next_turn.context is not None:
                        current_context = next_turn.context
                    prepared_messages = list(next_turn.messages or [])
                    stream_options = config.stream_options
                    if next_turn.thinking_level is not None:
                        stream_options = dataclasses.replace(
                            stream_options,
                            reasoning=to_thinking_level(next_turn.thinking_level),
                        )
                    config = dataclasses.replace(
                        config,
                        model=next_turn.model or config.model,
                        stream_options=stream_options,
                    )
                # 准备可能很慢；仅在当前没有选中输入时补轮询，避免
                # one-at-a-time 在同一回合越过队列顺序。
                if not pending_messages:
                    pending_messages = (
                        await invoke_hook(config.get_steering_messages, default=[])
                        or []
                    )

            if first_turn:
                first_turn = False
            else:
                await emit(TurnStartEvent())

            for message in _declare_tool_changes(
                current_context, [*prepared_messages, *pending_messages]
            ):
                await emit(MessageStartEvent(message=message))
                await emit(MessageEndEvent(message=message))
                current_context.messages.append(message)
                new_messages.append(message)
            pending_messages = []

            request_update = await invoke_hook(
                config.prepare_request,
                PrepareRequestContext(
                    context=current_context,
                    model=config.model,
                    thinking_level=(
                        ModelThinkingLevel(config.stream_options.reasoning.value)
                        if config.stream_options.reasoning is not None
                        else ModelThinkingLevel.OFF
                    ),
                ),
                signal,
            )
            if request_update is not None:
                if request_update.context is not None:
                    current_context = request_update.context
                stream_options = config.stream_options
                if request_update.thinking_level is not None:
                    stream_options = dataclasses.replace(
                        stream_options,
                        reasoning=to_thinking_level(request_update.thinking_level),
                    )
                config = dataclasses.replace(
                    config,
                    model=request_update.model or config.model,
                    stream_options=stream_options,
                )

            assistant_msg = await stream_assistant_response(
                current_context, config, signal, emit, stream_fn
            )
            new_messages.append(assistant_msg)

            if assistant_msg.stop_reason in (StopReason.ERROR, StopReason.ABORTED):
                last_completed_turn = PrepareNextTurnContext(
                    message=assistant_msg,
                    tool_results=[],
                    context=current_context,
                    new_messages=new_messages,
                    turn_index=turn_index,
                )
                # 错误/中止仍是硬退出；钩子可以观测，但决策被忽略。
                await invoke_hook(config.finish_turn, last_completed_turn, signal)
                await emit(TurnEndEvent(message=assistant_msg, tool_results=[]))
                await emit(AgentEndEvent(messages=new_messages))
                return

            tool_calls = [c for c in assistant_msg.content if c.type == "toolCall"]
            tool_results = []
            has_more_tool_calls = False
            if tool_calls:
                if assistant_msg.stop_reason == StopReason.LENGTH:
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

            last_completed_turn = PrepareNextTurnContext(
                message=assistant_msg,
                tool_results=tool_results,
                context=current_context,
                new_messages=new_messages,
                turn_index=turn_index,
            )
            decision = await invoke_hook(
                config.finish_turn, last_completed_turn, signal
            )
            await emit(TurnEndEvent(message=assistant_msg, tool_results=tool_results))
            if decision is not None and decision.action == "end":
                await emit(AgentEndEvent(messages=new_messages))
                return

            explicit_continuation = (
                decision is not None and decision.action == "continue"
            )
            pending_messages = (
                await invoke_hook(config.get_steering_messages, default=[]) or []
            )
            if has_more_tool_calls or pending_messages:
                explicit_continuation = False
            turn_index += 1

        follow_up = await invoke_hook(config.get_follow_up_messages, default=[]) or []
        if follow_up:
            explicit_continuation = False
            pending_messages = follow_up
            continue
        if explicit_continuation:
            # 没有自然输入时，仅补一次当前上下文请求；工具/steering/
            # follow-up 已选中请求时在上面的分支中消费掉这个标记。
            explicit_continuation = False
            continue
        break

    await emit(AgentEndEvent(messages=new_messages))
