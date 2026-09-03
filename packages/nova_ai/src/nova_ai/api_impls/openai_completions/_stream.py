"""流式调用主路径（对齐 TS ``stream`` / ``streamSimple``，2026-08 终态）。

相对旧移植的关键变化：

- ``stop_reason`` 以 ``pending`` 起步；无 ``finish_reason`` 时按内容推断
  （有 toolCall → ``toolUse``，否则 ``stop``）——不再直接报错；只有
  ``supports_finish_reason`` 的端点没收尾才报 "Stream ended without finish_reason"；
- ``raw_stop_reason`` 保留提供商原始 finish_reason；
- **reasoning details 终态**：流式 details 经 ``append_openai_reasoning_detail``
  拼接后归档进 thinking 块签名（JSON 数组），跨模型重放随块保留；
  旧的 toolCall.thought_signature 挂载路径删除（仅回放兜底读取）；
- 请求经 ``retry_provider_request`` 包装——SDK 内建重试不可被 abort 打断；
- nova 增强（保留）：异常路径 ``finish_all_blocks`` 幂等补发 end 事件；
  watchdog 任务在 abort 时主动关闭底层流（Python SDK 无 fetch 级 abort）。
"""

import asyncio
import inspect
import json
import time
from typing import Any, Dict, List, Optional, Set, Tuple, cast

import openai

from ...signal import AbortedError
from ...streaming import AssistantMessageEventStream
from ...types.content import TextContent, ThinkingContent, ToolCall
from ...types.enums import ModelThinkingLevel, StopReason
from ...types.events import (
    DoneEvent,
    ErrorEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from ...types.messages import AssistantMessage, Context
from ...types.model import Cost, Model, Usage
from ...types.stream_options import ProviderResponse, SimpleStreamOptions
from ...utils import calculate_cost
from ...utils.error_body import format_provider_error, normalize_provider_error
from ...utils.json_parser import StreamingJsonParser, parse_streaming_json
from ...utils.model_utils import clamp_thinking_level
from .._shared.prompt_cache import resolve_cache_retention
from .._shared.retry import RetryOptions, retry_provider_request
from .._shared.simple_options import build_base_options
from .client import _has_header, create_client
from .compat import get_compat
from .options import OpenAICompletionsOptions
from .params import build_params
from .reasoning import (
    append_openai_reasoning_detail,
    is_openai_reasoning_detail,
    parse_openai_reasoning_details,
)


def parse_chunk_usage(raw_usage: Any, model: Model) -> Usage:
    """解析 chunk 中的 usage 信息（对齐 TS parseChunkUsage）。

    cache_read 三源兜底：prompt_tokens_details.cached_tokens（OpenAI/
    OpenRouter）→ prompt_cache_hit_tokens（DeepSeek）→ cached_tokens（Kimi
    顶层字段）。cache_write 不从 cached_tokens 里扣——规范语义下它们是
    独立计数，扣掉会少报。
    """
    prompt_tokens = getattr(raw_usage, "prompt_tokens", 0) or 0

    prompt_tokens_details = getattr(raw_usage, "prompt_tokens_details", None)
    cache_read_tokens = (
        (getattr(prompt_tokens_details, "cached_tokens", 0) or 0)
        if prompt_tokens_details
        else 0
    )
    prompt_cache_hit = getattr(raw_usage, "prompt_cache_hit_tokens", 0) or 0
    cache_read_tokens = cache_read_tokens or prompt_cache_hit
    cache_read_tokens = cache_read_tokens or (
        getattr(raw_usage, "cached_tokens", 0) or 0
    )

    cache_write_tokens = (
        (getattr(prompt_tokens_details, "cache_write_tokens", 0) or 0)
        if prompt_tokens_details
        else 0
    )

    input_tokens = max(0, prompt_tokens - cache_read_tokens - cache_write_tokens)
    output_tokens = getattr(raw_usage, "completion_tokens", 0) or 0

    completion_tokens_details = getattr(raw_usage, "completion_tokens_details", None)
    reasoning_tokens = (
        (getattr(completion_tokens_details, "reasoning_tokens", 0) or 0)
        if completion_tokens_details
        else 0
    )

    usage = Usage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read_tokens,
        cache_write=cache_write_tokens,
        reasoning=reasoning_tokens,
        total_tokens=input_tokens
        + output_tokens
        + cache_read_tokens
        + cache_write_tokens,
        cost=Cost(),
    )
    calculate_cost(model, usage)
    return usage


def map_stop_reason(reason: Optional[str]) -> Tuple[StopReason, Optional[str]]:
    """映射 OpenAI finish_reason 到标准 StopReason（对齐 TS mapStopReason）。"""
    if reason is None:
        return StopReason.STOP, None
    if reason in ("stop", "end"):
        return StopReason.STOP, None
    if reason == "length":
        return StopReason.LENGTH, None
    if reason in ("function_call", "tool_calls"):
        return StopReason.TOOL_USE, None
    if reason == "content_filter":
        return StopReason.ERROR, "Provider finish_reason: content_filter"
    if reason == "network_error":
        return StopReason.ERROR, "Provider finish_reason: network_error"
    return StopReason.ERROR, f"Provider finish_reason: {reason}"


def stream(
    model: Model, context: Context, options: Optional[OpenAICompletionsOptions] = None
) -> AssistantMessageEventStream:
    """OpenAI Completions 流式处理主函数（对齐 TS stream 终态）。"""
    event_stream = AssistantMessageEventStream()

    async def process_stream() -> None:
        output = AssistantMessage(
            role="assistant",
            content=[],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage(
                input=0,
                output=0,
                cache_read=0,
                cache_write=0,
                total_tokens=0,
                cost=Cost(),
            ),
            # pending 起步：无 finish_reason 时按内容推断收尾（对齐 TS）
            stop_reason=StopReason.PENDING,
            timestamp=int(time.time() * 1000),
        )
        abort_watcher: Optional[asyncio.Task] = None
        # 提前绑定，保证请求阶段的早期异常也能走 finish_all_blocks 收尾
        blocks = output.content
        text_block: Optional[TextContent] = None
        thinking_block: Optional[ThinkingContent] = None
        has_finish_reason = False
        tool_call_blocks_by_index: Dict[int, ToolCall] = {}
        tool_call_blocks_by_id: Dict[str, ToolCall] = {}
        # 按 id(block) 持有的增量解析器——逐 delta 全量 json_repair 是 O(n²)，
        # 节流后全程修复工作量 O(n)（见 StreamingJsonParser）
        tool_call_parsers: Dict[int, StreamingJsonParser] = {}

        def get_content_index(block) -> int:
            # 按引用相等查找（对齐 JS indexOf）：pydantic 的 __eq__ 是按值比较，
            # 值相等的不同块对象会拿错 index，破坏 start/end 配对
            for i, b in enumerate(blocks):
                if b is block:
                    return i
            return -1

        # 已发 end 事件的块，保证任何终止路径下 end 不重不漏
        finished_content_indexes: Set[int] = set()

        def finish_block(block) -> None:
            if block is None:
                return
            content_index = get_content_index(block)
            if content_index == -1 or content_index in finished_content_indexes:
                return
            finished_content_indexes.add(content_index)
            if block.type == "text":
                event_stream.push(
                    TextEndEvent(
                        content_index=content_index,
                        content=block.text,
                        partial=output,
                    )
                )
            elif block.type == "thinking":
                event_stream.push(
                    ThinkingEndEvent(
                        content_index=content_index,
                        content=block.thinking,
                        partial=output,
                    )
                )
            elif block.type == "toolCall":
                parser = tool_call_parsers.get(id(block))
                parsed = parser.finish() if parser else parse_streaming_json(
                    block.partial_args
                )
                if isinstance(parsed, dict):
                    block.arguments = parsed
                block.partial_args = None
                block.stream_index = None
                event_stream.push(
                    ToolCallEndEvent(
                        content_index=content_index,
                        tool_call=block,
                        partial=output,
                    )
                )

        def finish_all_blocks() -> None:
            """为所有未闭合的块补发 end 事件（幂等）。"""
            for block in blocks:
                finish_block(block)

        def ensure_text_block() -> TextContent:
            nonlocal text_block
            if text_block is None:
                text_block = TextContent(type="text", text="")
                blocks.append(text_block)
                event_stream.push(
                    TextStartEvent(
                        content_index=get_content_index(text_block),
                        partial=output,
                    )
                )
            return text_block

        def ensure_thinking_block(thinking_signature: str) -> ThinkingContent:
            nonlocal thinking_block
            if thinking_block is None:
                thinking_block = ThinkingContent(
                    type="thinking",
                    thinking="",
                    thinking_signature=thinking_signature,
                )
                blocks.append(thinking_block)
                event_stream.push(
                    ThinkingStartEvent(
                        content_index=get_content_index(thinking_block),
                        partial=output,
                    )
                )
            return thinking_block

        def ensure_tool_call_block(tool_call_delta) -> ToolCall:
            stream_index = getattr(tool_call_delta, "index", None)
            if isinstance(stream_index, int):
                block = tool_call_blocks_by_index.get(stream_index)
            else:
                block = None

            tool_call_id = getattr(tool_call_delta, "id", None)
            if not block and tool_call_id:
                block = tool_call_blocks_by_id.get(tool_call_id)

            if not block:
                func = getattr(tool_call_delta, "function", None)
                block = ToolCall(
                    type="toolCall",
                    id=tool_call_id or "",
                    name=getattr(func, "name", "") if func else "",
                    arguments={},
                    partial_args="",
                    stream_index=stream_index,
                )
                if isinstance(stream_index, int):
                    tool_call_blocks_by_index[stream_index] = block
                if tool_call_id:
                    tool_call_blocks_by_id[tool_call_id] = block
                blocks.append(block)
                tool_call_parsers[id(block)] = StreamingJsonParser()
                event_stream.push(
                    ToolCallStartEvent(
                        content_index=get_content_index(block),
                        partial=output,
                    )
                )

            if isinstance(stream_index, int) and block.stream_index is None:
                block.stream_index = stream_index
                tool_call_blocks_by_index[stream_index] = block
            if tool_call_id:
                tool_call_blocks_by_id[tool_call_id] = block

            return block

        try:
            api_key = options.api_key if options else None
            compat = get_compat(model)
            cache_retention = resolve_cache_retention(
                options.cache_retention if options else None,
                options.env if options else None,
            )
            cache_session_id = (
                None
                if cache_retention == "none"
                else (options.session_id if options else None)
            )
            client = create_client(
                model,
                context,
                api_key,
                options.headers if options else None,
                session_id=cache_session_id,
                compat=compat,
            )
            params = build_params(model, context, options, compat, cache_retention)

            if options and options.on_payload:
                payload_result = options.on_payload(params, model)
                if inspect.isawaitable(payload_result):
                    payload_result = await payload_result
                params = payload_result or params

            timeout = options.timeout if options else None
            request_timeout = timeout if timeout is not None else openai.NOT_GIVEN

            signal = options.signal if options else None
            if signal and signal.aborted:
                raise AbortedError("Request was aborted")

            # SDK max_retries=0 + 本层重试：退避可被 signal 打断（对齐 TS）
            async def _request() -> Any:
                raw = await client.chat.completions.with_raw_response.create(
                    **params, timeout=request_timeout
                )
                return raw

            retry_options: RetryOptions = {
                "max_retries": (
                    options.max_retries
                    if options and options.max_retries is not None
                    else 0
                ),
                "signal": signal,
            }
            if options and options.max_retry_delay_ms is not None:
                retry_options["max_retry_delay_ms"] = options.max_retry_delay_ms
            raw_response = await retry_provider_request(_request, retry_options)
            if options and options.on_response:
                response_result = options.on_response(
                    ProviderResponse(
                        status=raw_response.status_code,
                        headers=dict(raw_response.headers),
                    ),
                    model,
                )
                if inspect.isawaitable(response_result):
                    await response_result
            openai_stream = raw_response.parse()

            # TS 的 OpenAI SDK 支持 fetch 级 signal abort；Python SDK 不支持，
            # 用看门狗任务在 abort 时主动关闭流，达到同等的即时中断效果。
            signal_wait = getattr(signal, "wait", None) if signal is not None else None
            if callable(signal_wait):

                async def _watch_abort() -> None:
                    try:
                        await signal_wait()
                        await openai_stream.close()
                    except Exception:
                        pass

                abort_watcher = asyncio.create_task(_watch_abort())

            event_stream.push(StartEvent(partial=output))

            async for chunk in openai_stream:
                if signal and signal.aborted:
                    await openai_stream.close()
                    break

                if not chunk or not hasattr(chunk, "choices"):
                    continue

                if hasattr(chunk, "id") and chunk.id and not output.response_id:
                    output.response_id = chunk.id

                if (
                    hasattr(chunk, "model")
                    and isinstance(chunk.model, str)
                    and chunk.model
                    and chunk.model != model.id
                    and not output.response_model
                ):
                    output.response_model = chunk.model

                if chunk.usage:
                    output.usage = parse_chunk_usage(chunk.usage, model)

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]

                # Fallback：部分 provider（如 Moonshot）把 usage 放在 choice 上
                if not chunk.usage and hasattr(choice, "usage") and choice.usage:
                    output.usage = parse_chunk_usage(choice.usage, model)

                if choice.finish_reason:
                    output.raw_stop_reason = choice.finish_reason
                    stop_reason, error_message = map_stop_reason(choice.finish_reason)
                    output.stop_reason = stop_reason
                    if error_message:
                        output.error_message = error_message
                    has_finish_reason = True

                if choice.delta:
                    delta = choice.delta

                    if delta.content and len(delta.content) > 0:
                        block = ensure_text_block()
                        block.text += delta.content
                        event_stream.push(
                            TextDeltaEvent(
                                content_index=get_content_index(block),
                                delta=delta.content,
                                partial=output,
                            )
                        )

                    delta_dict = (
                        delta.model_dump() if hasattr(delta, "model_dump") else {}
                    )
                    # 多字段 reasoning 兜底：取第一个非空字段避免重复
                    # （如 chutes.ai 同时返回 reasoning_content 与 reasoning）
                    reasoning_fields = [
                        "reasoning_content",
                        "reasoning",
                        "reasoning_text",
                    ]
                    found_reasoning = None
                    for field in reasoning_fields:
                        value = delta_dict.get(field)
                        if isinstance(value, str) and len(value) > 0:
                            found_reasoning = field
                            break

                    if found_reasoning:
                        thinking_signature = (
                            "reasoning_content"
                            if model.provider == "opencode-go"
                            and found_reasoning == "reasoning"
                            else found_reasoning
                        )
                        block = ensure_thinking_block(thinking_signature)
                        delta_text = delta_dict[found_reasoning]
                        block.thinking += delta_text
                        event_stream.push(
                            ThinkingDeltaEvent(
                                content_index=get_content_index(block),
                                delta=delta_text,
                                partial=output,
                            )
                        )

                    if delta.tool_calls:
                        for tool_call in delta.tool_calls:
                            block = ensure_tool_call_block(tool_call)
                            if not block.id and tool_call.id:
                                block.id = tool_call.id
                                tool_call_blocks_by_id[tool_call.id] = block

                            func = getattr(tool_call, "function", None)
                            if func and func.name and not block.name:
                                block.name = func.name

                            delta_args = ""
                            if func and func.arguments:
                                delta_args = func.arguments
                                block.partial_args = (
                                    block.partial_args or ""
                                ) + func.arguments
                                parser = tool_call_parsers.get(id(block))
                                if parser is not None:
                                    parser.feed(func.arguments)
                                    parsed = parser.value
                                    if isinstance(parsed, dict):
                                        block.arguments = parsed

                            content_index = get_content_index(block)
                            if content_index != -1:
                                event_stream.push(
                                    ToolCallDeltaEvent(
                                        content_index=content_index,
                                        delta=delta_args,
                                        partial=output,
                                    )
                                )

                    # reasoning details 终态：拼接后归档进 thinking 块签名。
                    # OpenRouter 以 delta 推送 details——相邻同类型 text/summary
                    # 合并为逻辑条目，encrypted 保持离散不透明。
                    if (
                        "reasoning_details" in delta_dict
                        and delta_dict["reasoning_details"]
                    ):
                        reasoning_details = delta_dict["reasoning_details"]
                        if isinstance(reasoning_details, list):
                            for detail in reasoning_details:
                                if not is_openai_reasoning_detail(detail):
                                    continue
                                block = ensure_thinking_block("")
                                # List 不变性：ReasoningDetail 归档形状在合并侧
                                # 作开放 dict 手术（运行时同为 dict），cast 收口
                                preserved = cast(
                                    List[Dict[str, Any]],
                                    parse_openai_reasoning_details(
                                        block.thinking_signature
                                    )
                                    or [],
                                )
                                append_openai_reasoning_detail(preserved, detail)
                                block.thinking_signature = json.dumps(preserved)

            finish_all_blocks()

            if (
                options
                and options.signal
                and hasattr(options.signal, "aborted")
                and options.signal.aborted
            ):
                raise AbortedError("Request was aborted")

            if output.stop_reason == StopReason.ABORTED:
                raise AbortedError("Request was aborted")
            if not has_finish_reason and not compat.supports_finish_reason:
                # 无 finish_reason 时按内容推断收尾（对齐 TS）
                output.stop_reason = (
                    StopReason.TOOL_USE
                    if any(b.type == "toolCall" for b in blocks)
                    else StopReason.STOP
                )
            if output.stop_reason == StopReason.ERROR:
                raise Exception(
                    output.error_message or "Provider returned an error stop reason"
                )
            if (compat.supports_finish_reason and not has_finish_reason) or (
                output.stop_reason == StopReason.PENDING
            ):
                raise Exception("Stream ended without finish_reason")

            event_stream.push(
                DoneEvent(reason=output.stop_reason, message=output)
            )
            event_stream.end()

        except Exception as e:
            # 任何异常路径也要先闭合所有未闭合的块（end 不重不漏），
            # 再推送 ErrorEvent 终止流
            finish_all_blocks()

            is_aborted = bool(options and options.signal and options.signal.aborted)
            output.stop_reason = StopReason.ABORTED if is_aborted else StopReason.ERROR

            normalized = normalize_provider_error(e)
            output.error_message = format_provider_error(normalized)

            raw_metadata = None
            try:
                raw_metadata = e.error.metadata.raw
            except Exception:
                pass
            if (
                isinstance(raw_metadata, str)
                and raw_metadata not in output.error_message
            ):
                output.error_message += f"\n{raw_metadata}"

            event_stream.push(
                ErrorEvent(reason=output.stop_reason, error=output)
            )
            event_stream.end()
        finally:
            if abort_watcher is not None:
                abort_watcher.cancel()

    asyncio.create_task(process_stream())
    return event_stream


def stream_simple(
    model: Model, context: Context, options: Optional[SimpleStreamOptions] = None
) -> AssistantMessageEventStream:
    """简化的 OpenAI Completions 流式处理（对齐 TS streamSimple 终态）。"""
    api_key = options.api_key if options else None
    headers = options.headers if options else None
    if (
        not api_key
        and not _has_header(headers, "authorization")
        and not _has_header(headers, "cf-aig-authorization")
    ):
        # 对齐 TS getClientApiKey：fail-fast 守卫——headers 自带 auth 时放行
        # （api_key 保持 None，由 headers 说话），否则报错，协议层不读环境变量。
        raise ValueError(f"No API key for provider: {model.provider}")

    base = build_base_options(model, context, options, api_key)

    reasoning_effort = None
    if options and options.reasoning:
        clamped = clamp_thinking_level(
            model, ModelThinkingLevel(options.reasoning.value)
        )
        if clamped != ModelThinkingLevel.OFF:
            reasoning_effort = clamped.value

    tool_choice = getattr(options, "tool_choice", None) if options else None

    openai_options = OpenAICompletionsOptions(
        temperature=base.temperature,
        max_tokens=base.max_tokens,
        signal=base.signal,
        api_key=base.api_key,
        transport=base.transport,
        cache_retention=base.cache_retention,
        session_id=base.session_id,
        headers=base.headers,
        env=base.env,
        on_payload=base.on_payload,
        on_response=base.on_response,
        metadata=base.metadata,
        timeout=base.timeout,
        websocket_connect_timeout_ms=base.websocket_connect_timeout_ms,
        max_retries=base.max_retries,
        max_retry_delay_ms=base.max_retry_delay_ms,
        sampling_params=base.sampling_params,
        thinking_budgets=options.thinking_budgets if options else None,
        tool_choice=tool_choice,
        reasoning_effort=reasoning_effort,
    )
    return stream(model, context, openai_options)
