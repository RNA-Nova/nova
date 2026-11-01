"""流式调用主路径（对齐 TS ``stream`` / ``streamSimple``）。

Anthropic 事件模型（SDK typed stream 承载）：``message_start``（首个
usage）→ ``content_block_start/delta/stop`` 按 ``index`` 归位（text /
thinking / redacted_thinking / tool_use）→ ``message_delta``（stop_reason
+ output usage 收尾）→ ``message_stop``。

关键不变量：

- **thinking 签名回放**：``signature_delta`` 逐段累积进 thinking 块的
  ``thinking_signature``，多轮回放原样回传（缺签名下一轮 400）；
  ``redacted_thinking`` 的不透明载荷收在签名位；
- **tool_use 参数流式拼装**：``input_json_delta`` 经 StreamingJsonParser
  增量解析（逐 delta 全量修复是 O(n²)，见 openai_completions 同款先例）；
- **usage 双段拼合**：input/cache 在 message_start，output 在 message_delta
  （代理端可能缺段——非 None 才覆盖）；total 由分量求和（Anthropic 不报
  total_tokens）；thinking tokens 读 ``output_tokens_details``；
- **取消**：请求+消费段包在 ``AbortScope`` 内——signal 触发即真死（anyio
  CancelScope，SDK 无 fetch 级 abort 的对策），归因在块后翻译；一切
  失败闭合所有块后以 error 终态收场。
"""

import inspect
import json
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union, cast

from nova_async import AbortScope
from nova_protocol import (
    AbortedError,
    AssistantMessage,
    CacheRetention,
    Context,
    Cost,
    DoneEvent,
    ErrorEvent,
    Model,
    ModelThinkingLevel,
    NovaErrorKind,
    StartEvent,
    StopReason,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    Usage,
    is_aborted,
    redact_secrets,
)

from ...stream_options import ModelIdentity, ProviderResponse, SimpleStreamOptions
from ...streaming import AssistantMessageEventStream
from ...utils import calculate_cost
from ...utils.errors import USAGE_LIMIT_PATTERNS, normalize_error
from ...utils.http import filter_headers_for_hook
from ...utils.json_parser import StreamingJsonParser, parse_streaming_json
from ...utils.model_utils import clamp_thinking_level
from .._shared.prompt_cache import resolve_cache_retention
from .._shared.retry import RetryOptions, retry_provider_request
from .._shared.simple_options import (
    adjust_max_tokens_for_thinking,
    build_base_options,
    clamp_max_tokens_to_context,
)
from .._shared.timeouts import (
    build_request_timeout_httpx2,
    idle_guarded,
    idle_timeout_of,
)
from .client import assert_request_auth, create_client
from .compat import get_compat
from .options import AnthropicEffort, AnthropicOptions
from .params import build_params

# 流式期内的内容块（文档化的可变累积器——数据建模标准规则 1 例外）
ContentBlock = Union[TextContent, ThinkingContent, ToolCall]

# Anthropic 错误体的 error.type → 归一分类（下划线形态不命中空格文案词表，
# 必须结构映射——codex 的 SSE error.code 映射表对位）。
# 注意 rate_limit_error 不在此：它同型覆盖"临时限流"与"额度用尽"两种，
# 分流归限额词表（归一阶梯第 3 级），此处不声明。
_ANTHROPIC_STREAM_ERROR_KINDS: Dict[str, NovaErrorKind] = {
    "overloaded_error": NovaErrorKind.OVERLOADED,
    "invalid_request_error": NovaErrorKind.BAD_REQUEST,
    "authentication_error": NovaErrorKind.UNAUTHORIZED,
    "permission_error": NovaErrorKind.UNAUTHORIZED,
    "not_found_error": NovaErrorKind.MODEL_NOT_FOUND,
    "billing_error": NovaErrorKind.USAGE_LIMIT_EXCEEDED,
    "timeout_error": NovaErrorKind.REQUEST_TIMEOUT,
    "api_error": NovaErrorKind.UPSTREAM_ERROR,
}


class _AnthropicStreamError(Exception):
    """SSE error 事件的归一输入（自声明 kind + 保留 body 供词表/排查）。"""

    def __init__(self, message: str, *, kind: NovaErrorKind, body: Any = None) -> None:
        super().__init__(message)
        self.nova_error_kind = kind
        if body is not None:
            self.body = body


def _remap_stream_error(error: Exception) -> Exception:
    """把 SDK 抛出的流内错误映射为自声明 kind 的领域形状。

    anthropic SDK 的 SSE 解码器把 error 事件转成 ``APIStatusError`` 抛出
    （``_streaming.py``——不 yield 给调用方），事件 JSON 在 ``body`` 里。
    本函数把 ``error.type`` 映射成 kind 贴回去；``rate_limit_error`` 先查
    限额词表（额度用尽让位给阶梯第 3 级，不声明）。其余异常原样返回。
    """
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return error
    err_obj = body.get("error")
    if not isinstance(err_obj, dict):
        return error
    error_type = err_obj.get("type")
    if not isinstance(error_type, str):
        return error
    if error_type == "rate_limit_error":
        # 同型两种语义：文案命中限额词表 → 不声明（归一第 3 级接管）；
        # 否则按临时限流声明
        text = f"{err_obj.get('message') or ''} {json.dumps(body, default=str)}"
        if any(p.search(text) for p in USAGE_LIMIT_PATTERNS):
            return error
        kind = NovaErrorKind.RATE_LIMITED
    else:
        kind = _ANTHROPIC_STREAM_ERROR_KINDS.get(error_type)
    if kind is None:
        return error
    message = err_obj.get("message")
    wrapped = _AnthropicStreamError(
        f"Anthropic stream error: {message or error}", kind=kind, body=body
    )
    wrapped.__cause__ = error
    return wrapped


_STOP_REASON_MAP: Dict[str, Tuple[StopReason, Optional[str]]] = {
    "end_turn": (StopReason.STOP, None),
    "max_tokens": (StopReason.LENGTH, None),
    "tool_use": (StopReason.TOOL_USE, None),
    # pause_turn / stop_sequence 按正常停止收场（对齐 TS：pause 已够好，
    # stop_sequence 我们不提供所以理论上不会出现）
    "pause_turn": (StopReason.STOP, None),
    "stop_sequence": (StopReason.STOP, None),
}


def map_stop_reason(
    reason: Optional[str], stop_details: Any = None
) -> Tuple[StopReason, Optional[str]]:
    """映射 Anthropic stop_reason 到标准 StopReason（对齐 TS mapStopReason）。"""
    if reason is None:
        return StopReason.PENDING, None
    if reason == "refusal":
        explanation = getattr(stop_details, "explanation", None)
        return (
            StopReason.ERROR,
            explanation or "The model refused to complete the request",
        )
    if reason == "sensitive":
        # 内容被安全过滤拦截（SDK 类型尚未收录——对齐 TS 注释）
        return StopReason.ERROR, "Provider stopped with: sensitive"
    mapped = _STOP_REASON_MAP.get(reason)
    if mapped is not None:
        return mapped
    # 未知 stop_reason：响亮失败（API 新增值时尽早暴露——对齐 TS default 分支）
    return StopReason.ERROR, f"Unhandled stop reason: {reason}"


def stream(
    model: Model, context: Context, options: Optional[AnthropicOptions] = None
) -> AssistantMessageEventStream:
    """Anthropic Messages 流式处理主函数（对齐 TS stream 终态）。"""
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
            stop_reason=StopReason.PENDING,
            timestamp=int(time.time() * 1000),
        )
        client = None
        owns_client = True
        anthropic_stream = None

        blocks: List[ContentBlock] = output.content
        # Anthropic 的 content_block 事件带 index——位置表按 index 归位
        positions: Dict[int, int] = {}
        tool_call_parsers: Dict[int, StreamingJsonParser] = {}
        finished_positions: Set[int] = set()

        def finish_block(position: int) -> None:
            if position in finished_positions:
                return
            finished_positions.add(position)
            block = blocks[position]
            if block.type == "text":
                event_stream.push(
                    TextEndEvent(
                        content_index=position, content=block.text, partial=output
                    )
                )
            elif block.type == "thinking":
                event_stream.push(
                    ThinkingEndEvent(
                        content_index=position, content=block.thinking, partial=output
                    )
                )
            elif block.type == "toolCall":
                parser = tool_call_parsers.get(position)
                parsed = (
                    parser.finish()
                    if parser is not None
                    else parse_streaming_json(block.partial_args)
                )
                if isinstance(parsed, dict):
                    block.arguments = parsed
                # 瞬态缓冲清零——回放只携带解析后的 arguments
                block.partial_args = None
                block.stream_index = None
                event_stream.push(
                    ToolCallEndEvent(
                        content_index=position, tool_call=block, partial=output
                    )
                )

        def finish_all_blocks() -> None:
            """为所有未闭合的块补发 end 事件（幂等）。"""
            for position in range(len(blocks)):
                finish_block(position)

        try:
            api_key = options.api_key if options else None
            options_headers = options.headers if options else None
            assert_request_auth(model.provider, api_key, options_headers)

            compat = get_compat(model)
            cache_retention = resolve_cache_retention(
                options.cache_retention if options else None,
                options.env if options else None,
            )
            cache_session_id = (
                None
                if cache_retention == CacheRetention.NONE
                else (options.session_id if options else None)
            )
            signal = options.signal if options else None
            if is_aborted(signal):
                raise AbortedError("Request was aborted")

            interleaved_option = (
                getattr(options, "interleaved_thinking", None) if options else None
            )
            interleaved_thinking = (
                interleaved_option if interleaved_option is not None else True
            )
            owns_client = options is None or getattr(options, "client", None) is None
            injected_client = getattr(options, "client", None) if options else None
            if injected_client is not None:
                # 注入的预建客户端（对齐 TS options.client）：跳过内部构造，
                # 属主归调用方（finally 不关闭）。
                # getattr 防御（TS ?. 对位）：高层路径传的可能是
                # StreamOptions 通用基类，不带协议专属字段
                client = injected_client
                is_oauth = False
                owns_client = False
            else:
                client, is_oauth = create_client(
                    model,
                    context,
                    api_key,
                    options_headers,
                    session_id=cache_session_id,
                    compat=compat,
                    interleaved_thinking=interleaved_thinking,
                )
            params = build_params(
                model,
                context,
                options,
                compat=compat,
                cache_retention=cache_retention,
                is_oauth=is_oauth,
            )

            if options and options.on_payload:
                payload_result = options.on_payload(
                    params, ModelIdentity.from_model(model)
                )
                if inspect.isawaitable(payload_result):
                    payload_result = await payload_result
                params = payload_result or params

            knob_s = options.timeout if options else None
            # connect/pool 恒工程上限；read/write 随旋钮（None=引擎默认，
            # 0=显式关闭）——超时治理见 _shared/timeouts.py
            request_kwargs: Dict[str, Any] = {
                "timeout": build_request_timeout_httpx2(knob_s)
            }

            # "stream" 是请求体语义（params 里带它供 on_payload 观测，对齐 TS
            # buildParams），SDK 的 create 由位置参数表达——调用边界剥掉防撞键
            create_kwargs = {k: v for k, v in params.items() if k != "stream"}

            # SDK max_retries=0 + 本层重试：退避可被 signal 打断（对齐 TS）。
            # with_raw_response.create 的协程 await 后直接拿到原始响应
            # （status/headers 供 on_response），await parse() 得类型化事件流
            async def _request():
                return await client.messages.with_raw_response.create(
                    stream=True, **create_kwargs, **request_kwargs
                )

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
            # 取消治理（anyio 换轨）：signal 触发即取消 scope——connect→TTFB
            # 与逐事件消费等一切块内等待真死（codex drop 对位）。scope 在块边界
            # 吞取消，归因在块后翻译；清理段（关流/关 client）在块外 finally——
            # level-triggered 语义下块内 await 会持续抛取消，cleanup 不进块。
            abort_scope = AbortScope(signal)
            with abort_scope:
                raw_response = await retry_provider_request(_request, retry_options)
                # 先建流再发钩子——钩子的 await 期间 abort/异常时 finally
                # 必关流（raw_response 到手与流建成之间不允许存在无管护窗口：
                # 注入 client（owns_client=False）的路径没有 client.close()
                # 兜底，连接只能靠 GC）
                anthropic_stream = await raw_response.parse()

                if options and options.on_response:
                    response_result = options.on_response(
                        ProviderResponse(
                            status=raw_response.status_code,
                            # 响应头进钩子前黑名单过滤（authorization/set-cookie
                            # 等凭据类永不出钩子的手——security 出口纪律）
                            headers=filter_headers_for_hook(dict(raw_response.headers)),
                        ),
                        ModelIdentity.from_model(model),
                    )
                    if inspect.isawaitable(response_result):
                        await response_result

                anthropic_stream = await raw_response.parse()

                event_stream.push(StartEvent(partial=output))

                async for event in idle_guarded(
                    anthropic_stream, idle_timeout_of(knob_s)
                ):
                    if event.type == "message_start":
                        output.response_id = event.message.id
                        output.response_model = event.message.model
                        usage = event.message.usage
                        output.usage.input = usage.input_tokens or 0
                        output.usage.output = usage.output_tokens or 0
                        output.usage.cache_read = usage.cache_read_input_tokens or 0
                        output.usage.cache_write = (
                            usage.cache_creation_input_tokens or 0
                        )
                        cache_creation = usage.cache_creation
                        if cache_creation is not None:
                            output.usage.cache_write_1h = (
                                cache_creation.ephemeral_1h_input_tokens
                            )
                        # Anthropic 不报 total_tokens——分量求和（对齐 TS）
                        output.usage.total_tokens = (
                            output.usage.input
                            + output.usage.output
                            + output.usage.cache_read
                            + output.usage.cache_write
                        )
                        calculate_cost(model, output.usage)

                    elif event.type == "content_block_start":
                        block_param = event.content_block
                        position = len(blocks)
                        positions[event.index] = position
                        if block_param.type == "text":
                            block = TextContent(
                                type="text", text=block_param.text or ""
                            )
                            blocks.append(block)
                            event_stream.push(
                                TextStartEvent(content_index=position, partial=output)
                            )
                        elif block_param.type == "thinking":
                            block = ThinkingContent(
                                type="thinking",
                                thinking=block_param.thinking or "",
                                thinking_signature=block_param.signature or "",
                            )
                            blocks.append(block)
                            event_stream.push(
                                ThinkingStartEvent(
                                    content_index=position, partial=output
                                )
                            )
                        elif block_param.type == "redacted_thinking":
                            # redacted：内容不可见，不透明载荷收签名位（对齐 TS）
                            block = ThinkingContent(
                                type="thinking",
                                thinking="[Reasoning redacted]",
                                thinking_signature=block_param.data,
                                redacted=True,
                            )
                            blocks.append(block)
                            event_stream.push(
                                ThinkingStartEvent(
                                    content_index=position, partial=output
                                )
                            )
                        elif block_param.type == "tool_use":
                            block = ToolCall(
                                type="toolCall",
                                id=block_param.id,
                                name=block_param.name,
                                arguments=dict(block_param.input or {}),
                                partial_args="",
                                stream_index=event.index,
                            )
                            blocks.append(block)
                            tool_call_parsers[position] = StreamingJsonParser()
                            event_stream.push(
                                ToolCallStartEvent(
                                    content_index=position, partial=output
                                )
                            )
                        else:
                            # 未知块类型（服务端新增块型）：占位跳过，不炸流
                            positions.pop(event.index, None)

                    elif event.type == "content_block_delta":
                        position = positions.get(event.index)
                        if position is None:
                            continue
                        block = blocks[position]
                        delta = event.delta
                        if delta.type == "text_delta" and block.type == "text":
                            block.text += delta.text
                            event_stream.push(
                                TextDeltaEvent(
                                    content_index=position,
                                    delta=delta.text,
                                    partial=output,
                                )
                            )
                        elif (
                            delta.type == "thinking_delta" and block.type == "thinking"
                        ):
                            block.thinking += delta.thinking
                            event_stream.push(
                                ThinkingDeltaEvent(
                                    content_index=position,
                                    delta=delta.thinking,
                                    partial=output,
                                )
                            )
                        elif (
                            delta.type == "input_json_delta"
                            and block.type == "toolCall"
                        ):
                            block.partial_args = (
                                block.partial_args or ""
                            ) + delta.partial_json
                            parser = tool_call_parsers.get(position)
                            if parser is not None:
                                parser.feed(delta.partial_json)
                                parsed = parser.value
                                if isinstance(parsed, dict):
                                    block.arguments = parsed
                            event_stream.push(
                                ToolCallDeltaEvent(
                                    content_index=position,
                                    delta=delta.partial_json,
                                    partial=output,
                                )
                            )
                        elif (
                            delta.type == "signature_delta" and block.type == "thinking"
                        ):
                            # 签名逐段累积，不产生事件（对齐 TS：签名非呈现内容）
                            block.thinking_signature = (
                                block.thinking_signature or ""
                            ) + delta.signature

                    elif event.type == "content_block_stop":
                        position = positions.get(event.index)
                        if position is not None:
                            finish_block(position)

                    elif event.type == "message_delta":
                        if event.delta.stop_reason:
                            output.raw_stop_reason = event.delta.stop_reason
                            stop_reason, error_message = map_stop_reason(
                                event.delta.stop_reason,
                                getattr(event.delta, "stop_details", None),
                            )
                            output.stop_reason = stop_reason
                            if error_message:
                                output.error_message = error_message
                        # 非 None 才覆盖（代理端可能在末段省略 input——保住
                        # message_start 的值，对齐 TS）
                        if event.usage:
                            if event.usage.input_tokens is not None:
                                output.usage.input = event.usage.input_tokens
                            if event.usage.output_tokens is not None:
                                output.usage.output = event.usage.output_tokens
                            if event.usage.cache_read_input_tokens is not None:
                                output.usage.cache_read = (
                                    event.usage.cache_read_input_tokens
                                )
                            if event.usage.cache_creation_input_tokens is not None:
                                output.usage.cache_write = (
                                    event.usage.cache_creation_input_tokens
                                )
                            # thinking tokens 在 output_tokens_details.thinking_tokens
                            # （SDK 类型可能滞后——读 dump 视图，对齐 TS 的窄 cast）
                            details = (
                                event.usage.model_dump().get("output_tokens_details")
                                or {}
                            )
                            thinking_tokens = details.get("thinking_tokens")
                            if thinking_tokens is not None:
                                output.usage.reasoning = int(thinking_tokens)
                            output.usage.total_tokens = (
                                output.usage.input
                                + output.usage.output
                                + output.usage.cache_read
                                + output.usage.cache_write
                            )
                            calculate_cost(model, output.usage)

                finish_all_blocks()

                # 截断检测（有意分歧 TS：TS 手解 SSE 能看到 message_stop；SDK 的
                # typed stream 不产出它）——stop_reason 是我们的终态信号：
                # 提前断流必然没有 message_delta，stop_reason 停在 PENDING
                if output.stop_reason == StopReason.PENDING:
                    raise Exception("Anthropic stream ended without a stop reason")
                if output.stop_reason in (StopReason.ABORTED, StopReason.ERROR):
                    raise Exception(output.error_message or "An unknown error occurred")

                event_stream.push(DoneEvent(reason=output.stop_reason, message=output))
                event_stream.end()
            abort_scope.raise_if_aborted()

        except Exception as error:
            # 流已终结（DoneEvent 已发布）时，迟到的异常不得改写已发布消息——
            # Done→abort 竞态：scope 杀不到已完成的块，举旗抛错若改写
            # stop_reason，消费者手持 Done 却看到 ABORTED（openai 同款防护）
            if event_stream.done:
                return
            # 任何异常路径：先闭合所有未闭合的块（end 不重不漏），再 error 终态
            finish_all_blocks()
            # 取消判定：旗标 + 类型双通道（scope 关流竞态下 AbortedError
            # 可能在旗标未置位时到达——取消永远不是错误，铁律三）
            aborted = (
                options is not None and is_aborted(options.signal)
            ) or isinstance(error, AbortedError)
            output.stop_reason = StopReason.ABORTED if aborted else StopReason.ERROR
            if aborted:
                # 取消路径不归一（normalize_error 契约）——kind/retry_after
                # 永不上；消息即原话（过出口清洗）
                output.error_message = redact_secrets(
                    str(error) or "Request was aborted"
                )
            else:
                # SDK 的 SSE 解码器把 error 事件转成 APIStatusError 抛出（不
                # yield）——先经 _remap_stream_error 把 error.type 贴回自声明
                # kind，再归一
                normalized_error = normalize_error(
                    _remap_stream_error(error), domain="stream"
                )
                output.error_message = normalized_error.message
                if output.error_kind is None:
                    output.error_kind = normalized_error.kind
                if normalized_error.retry_after_ms is not None:
                    output.retry_after_ms = int(normalized_error.retry_after_ms)
            event_stream.push(ErrorEvent(reason=output.stop_reason.value, error=output))
            event_stream.end()
        finally:
            # 关闭序：流 → 客户端（防连接池异步生成器活到循环拆除，
            # openai_completions 同款纪律；注入的客户端归调用方不关闭）
            if anthropic_stream is not None:
                try:
                    await anthropic_stream.close()
                except Exception:
                    pass
            if client is not None and owns_client:
                try:
                    await client.close()
                except Exception:
                    pass

    # 驱动任务归流自持（防 GC 中途回收）
    event_stream.drive(process_stream())
    return event_stream


def _map_thinking_level_to_effort(model: Model, level: Any) -> AnthropicEffort:
    """思考级别 → adaptive effort（对齐 TS mapThinkingLevelToEffort：
    level_map 映射优先，未映射按档回落）。"""
    mapped = (model.thinking_level_map or {}).get(level.value) if level else None
    if isinstance(mapped, str):
        return cast(AnthropicEffort, mapped)
    if level is not None and level.value in ("minimal", "low"):
        return "low"
    if level is not None and level.value == "medium":
        return "medium"
    if level is not None and level.value == "high":
        return "high"
    # xhigh/max 等未映射档回落 high（对齐 TS default 分支）
    return "high"


def stream_simple(
    model: Model, context: Context, options: Optional[SimpleStreamOptions] = None
) -> AssistantMessageEventStream:
    """简化的 Anthropic Messages 流式处理（对齐 TS streamSimple 终态）。

    思考映射（对齐 TS）：不请求思考 → 显式关闭；adaptive 模型 → effort；
    旧模型 → 预算制（预算与 max_tokens 联合调整 + 窗口钳制）。
    """
    api_key = options.api_key if options else None
    headers = options.headers if options else None
    # fail-fast 守卫（对齐 TS assertRequestAuth）：协议层不读环境变量
    assert_request_auth(model.provider, api_key, headers)

    base = build_base_options(model, context, options, api_key)
    compat = get_compat(model)

    if options is None or not options.reasoning:
        thinking_enabled = False
        effort = None
        thinking_budget_tokens = None
        max_tokens = base.max_tokens
    elif compat.force_adaptive_thinking:
        thinking_enabled = True
        effort = _map_thinking_level_to_effort(model, options.reasoning)
        thinking_budget_tokens = None
        max_tokens = base.max_tokens
    else:
        thinking_enabled = True
        effort = None
        adjusted_max, adjusted_budget = adjust_max_tokens_for_thinking(
            base.max_tokens,
            model.max_tokens,
            options.reasoning,
            options.thinking_budgets,
        )
        max_tokens = clamp_max_tokens_to_context(model, context, adjusted_max)
        # 预算与答案共享 max_tokens——给答案留 1024（对齐 TS）
        thinking_budget_tokens = min(adjusted_budget, max(0, max_tokens - 1024))

    anthropic_options = AnthropicOptions(
        # 逐字段拷贝有意为之（asdict 会 deepcopy signal——克隆取消信号是
        # bug 温床；replace 不能换类；StreamOptions 新增字段时这里必须同步）
        temperature=base.temperature,
        max_tokens=max_tokens,
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
        max_retries=base.max_retries,
        max_retry_delay_ms=base.max_retry_delay_ms,
        sampling_params=base.sampling_params,
        thinking_enabled=thinking_enabled,
        thinking_budget_tokens=thinking_budget_tokens,
        effort=effort,
        tool_choice=getattr(options, "tool_choice", None) if options else None,
    )
    return stream(model, context, anthropic_options)


__all__ = ["map_stop_reason", "stream", "stream_simple"]
