"""provider 流消费、消息归约与 driver 收尾。"""

import asyncio
import dataclasses
import logging
from typing import Optional, cast

from nova_ai import NOVA_ATTRIBUTION, AssistantMessageEventStream, merge_client_identity
from nova_async import AbortController, AbortSignal, any_signal
from nova_protocol import (
    AssistantMessage,
    ClientIdentity,
    Context,
    DoneEvent,
    ErrorEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ModelThinkingLevel,
    StartEvent,
)

from ..stream_fn import get_default_stream_fn
from ..types import AgentContext, AgentEventSink, AgentLoopConfig, StreamFn
from ..utils import default_convert_to_llm, invoke_hook

DEFAULT_AGENT_IDENTITY = ClientIdentity(attribution=NOVA_ATTRIBUTION)
"""Agent 循环默认归因；调用方 identity 逐字段覆盖，显式 null 可关闭。"""

logger = logging.getLogger(__name__)


async def _finish_driver(
    response: AssistantMessageEventStream,
    request_controller: AbortController,
    completed: bool,
) -> None:
    """按完成分野收尾 driver；取消后的 join 也必须有界。"""
    driver = response.task
    if driver is None:
        return
    # gather 负责消费 driver 的迟到异常；shield 防止等待者取消毒化 join。
    # 必须在首个 await 前建立，覆盖 abort 后第一次让出事件循环的取消窗口。
    join = asyncio.gather(driver, return_exceptions=True)
    try:
        if not completed and not driver.done():
            request_controller.abort()
            # 给 provider 一个按 signal 收尾的机会，再进入有界 join。
            await asyncio.sleep(0)
        async with asyncio.timeout(0.1):
            await asyncio.shield(join)
    except TimeoutError:
        if not driver.done():
            # 只发硬取消并交付一次调度，不再无界等待第三方 driver。
            driver.cancel()
            await asyncio.sleep(0)
        logger.warning(
            "Provider driver did not finish within 100ms of cleanup; "
            "proceeding with terminal events"
        )
    except asyncio.CancelledError:
        if not driver.done():
            driver.cancel()
        raise


async def stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    signal: Optional[AbortSignal],
    emit: AgentEventSink,
    stream_fn: Optional[StreamFn],
) -> AssistantMessage:
    """转录 → LLM 消息 → 流式回复；provider 上下文只从转录构建。"""
    messages = await invoke_hook(
        config.transform_context, context.messages, signal, default=context.messages
    )
    convert = config.convert_to_llm or default_convert_to_llm
    llm_messages = await invoke_hook(convert, messages)
    llm_context = Context(messages=llm_messages)

    stream_func = stream_fn or get_default_stream_fn()
    resolved_api_key = config.stream_options.api_key
    resolved = await invoke_hook(config.get_api_key, config.model.provider)
    if resolved:
        resolved_api_key = resolved
    # 每个请求一个本地取消源；父 signal 经 any 组合，提前退出时只取消本请求。
    request_controller = AbortController(name="agent-request")
    request_signal = any_signal([signal, request_controller.signal])
    stream_options = dataclasses.replace(
        config.stream_options,
        api_key=resolved_api_key,
        identity=merge_client_identity(
            DEFAULT_AGENT_IDENTITY, config.stream_options.identity
        ),
        signal=request_signal,
    )
    response = cast(
        AssistantMessageEventStream,
        await invoke_hook(stream_func, config.model, llm_context, stream_options),
    )

    async def final_result() -> AssistantMessage:
        message = await response.result()
        # 记录 Agent 实际请求的级别；provider_thinking_level 是另一份端点事实。
        message.thinking_level = (
            ModelThinkingLevel(stream_options.reasoning.value)
            if stream_options.reasoning is not None
            else ModelThinkingLevel.OFF
        )
        return message

    completed = False
    try:
        partial_message: Optional[AssistantMessage] = None
        added_partial = False
        async for event in response:
            if isinstance(event, StartEvent):
                partial_message = event.partial
                context.messages.append(partial_message)
                added_partial = True
                await emit(MessageStartEvent(message=partial_message.model_copy()))
            elif not isinstance(event, (DoneEvent, ErrorEvent)):
                if partial_message is not None:
                    partial_message = event.partial
                    context.messages[-1] = partial_message
                    await emit(
                        MessageUpdateEvent(
                            assistant_message_event=event,
                            message=partial_message.model_copy(),
                        )
                    )
            else:
                final_message = await final_result()
                completed = True
                if added_partial:
                    context.messages[-1] = final_message
                else:
                    context.messages.append(final_message)
                if not added_partial:
                    await emit(MessageStartEvent(message=final_message.model_copy()))
                await emit(MessageEndEvent(message=final_message))
                return final_message

        final_message = await final_result()
        completed = True
        if added_partial:
            context.messages[-1] = final_message
        else:
            context.messages.append(final_message)
            await emit(MessageStartEvent(message=final_message.model_copy()))
        await emit(MessageEndEvent(message=final_message))
        return final_message
    finally:
        await _finish_driver(response, request_controller, completed)


__all__ = ["DEFAULT_AGENT_IDENTITY", "stream_assistant_response"]
