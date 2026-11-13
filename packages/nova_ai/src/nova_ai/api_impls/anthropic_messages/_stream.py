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

from anthropic import omit
from anthropic.types import RawMessageStreamEvent
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

from ...gateway.streams import lazy_stream
from ...stream_options import ModelIdentity, ProviderResponse, SimpleStreamOptions
from ...streaming import AssistantMessageEventStream
from ...utils import calculate_cost
from ...utils.diagnostics import append_diagnostic
from ...utils.errors import (
    USAGE_LIMIT_PATTERNS,
    StreamDisconnectedError,
    normalize_error,
)
from ...utils.http import filter_headers_for_hook
from ...utils.json_parser import StreamingJsonParser, parse_tool_call_arguments
from .._shared.openrouter_cost import apply_openrouter_reported_cost
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
from .._shared.transcript import (
    get_current_tools,
    normalize_context,
    resolve_transcript,
)
from .client import (
    anthropic_federation_config,
    assert_request_auth,
    build_beta_features,
    create_client,
    resolve_beta_features,
)
from .compat import get_compat
from .constrained import is_strict_tool_limit_error_message
from .options import AnthropicEffort, AnthropicOptions
from .params import build_params_with_meta, resolve_active_effort
from .sse import AnthropicEventStream
from .threads import (
    PreparedThreadRequest,
    prepare_thread_request,
    record_thread_reply,
    send_thread_request,
)
from .tool_names import from_claude_code_name

# 流式期内的内容块（文档化的可变累积器——数据建模标准规则 1 例外）
ContentBlock = Union[TextContent, ThinkingContent, ToolCall]


def _extra_field(entry: Any, name: str) -> Any:
    """SDK extra 残余的边界读取（dict/对象双形态；仅此归一口使用）。"""
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def _error_type_of(error: BaseException) -> Optional[str]:
    """SDK 错误的 ``error.type``（body.error.type 优先，裸 ``type`` 属性兜底）。"""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict):
            type_value = inner.get("type")
            if isinstance(type_value, str):
                return type_value
    type_attr = getattr(error, "type", None)
    return type_attr if isinstance(type_attr, str) else None


# SDK ``AsyncMessages.create()`` 的声明命名参数（anthropic SDK 1.x——
# 声明集外的请求字段经 extra_body 上线，见 ``_send``）
_SDK_CREATE_KWARG_KEYS = frozenset(
    [
        "max_tokens",
        "messages",
        "model",
        "cache_control",
        "container",
        "inference_geo",
        "metadata",
        "output_config",
        "service_tier",
        "stop_sequences",
        "system",
        "thinking",
        "tool_choice",
        "tools",
        "user_profile_id",
        "workspace_id",
    ]
)

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
    if error_type == "invalid_request_error":
        # 通用 400 不得压过更具体的 overflow/quota 文案分类。
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
        # mid-convo effort：记录当前档供后续请求回放（对齐 TS
        # providerThinkingLevel——effort 标记发出时同步记录；失败/中断的
        # 回复不落盘，维持"只记完整回复"的对齐不变量）
        compat = get_compat(model)
        active_effort = resolve_active_effort(model, options, compat)
        if active_effort is not None:
            output.provider_thinking_level = active_effort
        client = None
        owns_client = True
        anthropic_stream = None

        blocks: List[ContentBlock] = output.content
        # Anthropic 的 content_block 事件带 index——位置表按 index 归位
        positions: Dict[int, int] = {}
        tool_call_parsers: Dict[int, StreamingJsonParser] = {}
        finished_positions: Set[int] = set()
        # 成本计费模型（服务端 fallback 应答时换 fallback 条目的本地定价——
        # 对齐 TS usageModel；message_start 处改写）
        usage_model = model
        # 服务端对入参的改写留痕（对齐 TS inputTransformations——
        # message_start / message_delta 两处读，成功收尾落诊断）
        input_transformations: Optional[List[Any]] = None
        saw_message_start = False
        saw_message_stop = False

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
                # 起始 tool_use.input 是 SDK 担保的完整字典；只有实际收到
                # input_json_delta 时才以累积文本的严格终值覆盖它。
                if block.partial_args:
                    parsed = parse_tool_call_arguments(block.partial_args)
                    block.arguments = parsed.arguments
                    block.arguments_error = parsed.arguments_error
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

            compat = get_compat(model)
            current_tools = get_current_tools(
                resolve_transcript(
                    normalize_context(context),
                    compat.supports_mid_convo_system_messages,
                )
            )
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
                # 属主归调用方（finally 不关闭）。注入路径不做鉴权断言
                # （对齐 TS——客户端自带鉴权情形合法，如 Vertex 变体）
                # 与 pi 的 requestOptions.maxRetries=0 对位：派生客户端共享
                # 原传输与凭据缓存，但关闭不可取消的 SDK 内建重试；所有权仍
                # 归调用方，finally 不关闭它或原 client。
                client = injected_client.with_options(max_retries=0)
                is_oauth = False
                owns_client = False
            else:
                federation = anthropic_federation_config(
                    model,
                    api_key,
                    options_headers,
                    options.env if options else None,
                )
                # 自建路径才断言鉴权；完整 federation 配置本身就是凭据来源。
                if federation is None:
                    assert_request_auth(model.provider, api_key, options_headers)
                client, is_oauth = create_client(
                    model,
                    context,
                    api_key,
                    options_headers,
                    session_id=cache_session_id,
                    compat=compat,
                    interleaved_thinking=interleaved_thinking,
                    # pi `thinkingEnabled === true` 严格语义（显式真才算）
                    thinking_enabled=getattr(options, "thinking_enabled", None) is True,
                    federation=federation,
                    identity=options.identity if options else None,
                )
                # federation 只共享基座的 token cache；本请求新建的派生 client
                # 仍归本流关闭，基座与共享 token cache 不随请求销毁。
                owns_client = True
            knob_s = options.timeout if options else None
            # connect/pool 恒工程上限；read/write 随旋钮（None=引擎默认，
            # 0=显式关闭）——超时治理见 _shared/timeouts.py
            request_kwargs: Dict[str, Any] = {
                "timeout": build_request_timeout_httpx2(knob_s)
            }

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

            # message threads 会话（对齐 pi threadSessionId）：缓存开启且
            # 端点支持时激活；beta 列表与指纹同源（build_beta_features）
            thread_session_id = (
                cache_session_id if compat.supports_message_threads else None
            )
            beta_resolution = resolve_beta_features(
                model,
                options_headers,
                build_beta_features(
                    model,
                    context,
                    compat,
                    interleaved_thinking=interleaved_thinking,
                    thinking_enabled=getattr(options, "thinking_enabled", None) is True,
                    session_id=thread_session_id,
                ),
            )
            betas = beta_resolution.values
            disable_continuation = bool(
                getattr(options, "disable_continuation", None) if options else False
            )

            async def _send(body: Dict[str, Any]) -> Any:
                """单次发送（body = params 或线程请求体）。

                边界纪律：SDK ``create()`` 只收声明命名参数（无 **kwargs，
                未知键即 TypeError）——声明集内的键走 kwargs，其余
                （``thread``/``fallbacks``/``temperature`` 等 beta 或
                自由负载）统归 ``extra_body`` 合入线上 JSON 顶层；
                ``stream`` 是请求体语义（params 里带它供 on_payload
                观测），SDK 由位置参数表达——剥掉防撞键。注入客户端
                （非自建）时 beta 按请求级 ``extra_headers`` 下发——
                注入客户端没有我们的 default_headers（pi 的 betas 本来就是
                请求参数）。
                """
                request_params: Dict[str, Any] = {}
                extra_body: Dict[str, Any] = dict(body.get("extra_body") or {})
                for key, value in body.items():
                    if key == "stream":
                        continue
                    if key in _SDK_CREATE_KWARG_KEYS:
                        request_params[key] = value
                    elif key != "extra_body":
                        extra_body[key] = value
                if extra_body:
                    request_params["extra_body"] = extra_body
                if not owns_client and beta_resolution.overridden:
                    request_params["extra_headers"] = {
                        "anthropic-beta": (
                            omit
                            if beta_resolution.raw_override is None
                            else beta_resolution.raw_override
                        )
                    }
                elif not owns_client and betas:
                    request_params["extra_headers"] = {
                        "anthropic-beta": ",".join(betas)
                    }
                return await client.messages.with_raw_response.create(
                    stream=True, **request_params, **request_kwargs
                )

            async def _send_retried(body: Dict[str, Any]) -> Any:
                return await retry_provider_request(lambda: _send(body), retry_options)

            # 发送循环（对齐 pi stream 的 for(;;)）：strict 限量 400 → prefer
            # 工具降级非严格重发一次；threads 的拒绝归 send_thread_request
            disable_preferred_strict = False
            thread: Optional[PreparedThreadRequest] = None
            # 取消治理（anyio 换轨）：signal 触发即取消 scope——connect→TTFB
            # 与逐事件消费等一切块内等待真死（codex drop 对位）。scope 在块边界
            # 吞取消，归因在块后翻译；清理段（关流/关 client）在块外 finally——
            # level-triggered 语义下块内 await 会持续抛取消，cleanup 不进块。
            abort_scope = AbortScope(signal)
            with abort_scope:
                while True:
                    if is_aborted(signal):
                        raise AbortedError("Request was aborted")
                    params, has_preferred_strict = build_params_with_meta(
                        model,
                        context,
                        options,
                        compat=compat,
                        cache_retention=cache_retention,
                        is_oauth=is_oauth,
                        disable_preferred_strict_tools=disable_preferred_strict,
                    )
                    if options and options.on_payload:
                        payload_result = options.on_payload(
                            params, ModelIdentity.from_model(model)
                        )
                        if inspect.isawaitable(payload_result):
                            payload_result = await payload_result
                        # 对齐 TS：仅 None 保留原 params（空 dict 等 falsy 也是合法替换）
                        params = (
                            payload_result if payload_result is not None else params
                        )

                    prepared = (
                        prepare_thread_request(model, thread_session_id, params, betas)
                        if thread_session_id is not None and not disable_continuation
                        else None
                    )
                    try:
                        if prepared is not None:
                            raw_response, thread = await send_thread_request(
                                prepared, params, _send_retried, output, signal
                            )
                        else:
                            raw_response = await _send_retried(params)
                            thread = None
                        break
                    except Exception as error:
                        # 只降级被拒的请求，永不降级已开始的流（对齐 pi catch——
                        # 含 error.type == invalid_request_error 校验）
                        if (
                            is_aborted(signal)
                            or disable_preferred_strict
                            or not has_preferred_strict
                            or getattr(error, "status_code", None) != 400
                            or _error_type_of(error) != "invalid_request_error"
                            or not is_strict_tool_limit_error_message(str(error))
                        ):
                            raise
                        disable_preferred_strict = True
                        append_diagnostic(
                            output,
                            {
                                "type": "anthropic_strict_tools_fallback",
                                "error": {"message": str(error)},
                                "details": {"reason": "strict_tool_limits_exceeded"},
                            },
                        )
                # 先建流再发钩子——钩子的 await 期间 abort/异常时 finally
                # 必关流（raw_response 到手与流建成之间不允许存在无管护窗口：
                # 注入 client（owns_client=False）的路径没有 client.close()
                # 兜底，连接只能靠 GC）
                anthropic_stream = AnthropicEventStream(
                    cast_to=cast(Any, RawMessageStreamEvent),
                    response=raw_response.http_response,
                    client=client,
                )

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

                event_stream.push(StartEvent(partial=output))

                async for event in idle_guarded(
                    anthropic_stream, idle_timeout_of(knob_s)
                ):
                    if options and options.on_provider_stream_event:
                        callback_result = options.on_provider_stream_event(
                            event, ModelIdentity.from_model(model)
                        )
                        if inspect.isawaitable(callback_result):
                            await callback_result
                    if event.type == "message_start":
                        saw_message_start = True
                    elif event.type == "message_stop":
                        saw_message_stop = True
                    if event.type == "message_start":
                        output.response_id = event.message.id
                        # 仅当实际服务模型与请求不同才落（对齐 TS——
                        # response_model 是"被改写"信号；threads 据其缺席
                        # 判定回复可续接）
                        if event.message.model != model.id:
                            output.response_model = event.message.model
                            # 服务端 fallback 应答：按 fallback 条目的本地
                            # 定价计成本（对齐 TS usageModel 解析）
                            fallback_entry = next(
                                (
                                    entry
                                    for entry in (compat.allowed_fallback_models or [])
                                    if entry.provider == model.provider
                                    and entry.model == output.response_model
                                ),
                                None,
                            )
                            if fallback_entry is not None:
                                usage_model = model.model_copy(
                                    update={
                                        "id": output.response_model,
                                        "cost": fallback_entry.cost,
                                    }
                                )
                        # 服务端对入参的改写留痕（SDK extra 直通——对齐 TS
                        # `Array.isArray` 防御：非 list 不取）
                        _start_transformations = getattr(
                            event.message, "input_transformations", None
                        )
                        if isinstance(_start_transformations, list):
                            input_transformations = _start_transformations
                        usage = event.message.usage
                        output.usage.input = usage.input_tokens or 0
                        output.usage.output = usage.output_tokens or 0
                        output.usage.cache_read = usage.cache_read_input_tokens or 0
                        output.usage.cache_write = (
                            usage.cache_creation_input_tokens or 0
                        )
                        cache_creation = _extra_field(usage, "cache_creation")
                        if cache_creation is not None:
                            output.usage.cache_write_1h = (
                                _extra_field(
                                    cache_creation, "ephemeral_1h_input_tokens"
                                )
                                or 0
                            )
                        # Anthropic 不报 total_tokens——分量求和（对齐 TS）
                        output.usage.total_tokens = (
                            output.usage.input
                            + output.usage.output
                            + output.usage.cache_read
                            + output.usage.cache_write
                        )
                        calculate_cost(usage_model, output.usage)
                        apply_openrouter_reported_cost(output.usage, usage, usage_model)

                    elif event.type == "content_block_start":
                        block_param = event.content_block
                        if block_param.type == "fallback":
                            # 服务端 refusal fallback 的标记块（对齐 TS）：
                            # 只许出现在输出开头——中途换模型不支持
                            if blocks:
                                raise Exception(
                                    "Anthropic performed an unsupported mid-output model fallback"
                                )
                            continue
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
                                name=(
                                    from_claude_code_name(
                                        block_param.name, current_tools
                                    )
                                    if is_oauth
                                    else block_param.name
                                ),
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
                            # 已关闭的块不再接受晚到 delta；否则 text_end 与最终
                            # 消息会分裂，tool 终值也可能被重写。
                            positions.pop(event.index, None)
                            tool_call_parsers.pop(position, None)

                    elif event.type == "message_delta":
                        # 服务端入参改写留痕（对齐 TS——message_delta 处覆盖式再读）
                        _delta_transformations = getattr(
                            event, "input_transformations", None
                        )
                        if isinstance(_delta_transformations, list):
                            input_transformations = _delta_transformations
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
                            # 1h 缓存细分（对齐 TS——Vercel 网关在 delta 段报
                            # ephemeral_1h_input_tokens；SDK extra 直通）
                            _cache_creation = _extra_field(
                                event.usage, "cache_creation"
                            )
                            if _cache_creation is not None:
                                _e1h = _extra_field(
                                    _cache_creation, "ephemeral_1h_input_tokens"
                                )
                                if _e1h is not None:
                                    output.usage.cache_write_1h = _e1h
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
                            calculate_cost(usage_model, output.usage)
                            apply_openrouter_reported_cost(
                                output.usage, event.usage, usage_model
                            )

                finish_all_blocks()

                # SDK typed stream 同样产出 message_stop。见过 start 却缺 stop
                # 即传输截断；不得成功收尾，更不得写入 thread 台账。
                if saw_message_start and not saw_message_stop:
                    raise StreamDisconnectedError(
                        "Anthropic stream ended before message_stop"
                    )
                if output.stop_reason == StopReason.PENDING:
                    raise StreamDisconnectedError(
                        "Anthropic stream ended without a stop reason"
                    )
                if output.stop_reason in (StopReason.ABORTED, StopReason.ERROR):
                    raise Exception(output.error_message or "An unknown error occurred")

                # 干净回复落账：会话下一请求可从此续接（对齐 pi recordThreadReply）
                if thread is not None:
                    record_thread_reply(
                        model, thread, params, output, compat, betas, is_oauth=is_oauth
                    )

                # 服务端入参改写留痕上诊断（对齐 TS anthropic_input_transformations）
                if input_transformations:
                    append_diagnostic(
                        output,
                        {
                            "type": "anthropic_input_transformations",
                            "details": {
                                "transformations": [
                                    {
                                        "type": _extra_field(t, "type"),
                                        "path": _extra_field(t, "path"),
                                        "reason": _extra_field(t, "reason"),
                                    }
                                    for t in input_transformations
                                ]
                            },
                        },
                    )

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
                    _remap_stream_error(error), domain="stream", provider=model.provider
                )
                output.error_message = normalized_error.message
                if output.error_kind is None:
                    output.error_kind = normalized_error.kind
                if normalized_error.retry_after_ms is not None:
                    output.retry_after_ms = int(normalized_error.retry_after_ms)
            event_stream.push(ErrorEvent(reason=output.stop_reason, error=output))
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
    """简化入口；装配、鉴权与预算失败同样经事件流收尾，不同步抛出。"""

    async def _setup() -> AssistantMessageEventStream:
        return _stream_simple(model, context, options)

    return lazy_stream(model, _setup, options.signal if options else None)


def _stream_simple(
    model: Model, context: Context, options: Optional[SimpleStreamOptions] = None
) -> AssistantMessageEventStream:
    """简化的 Anthropic Messages 流式处理（对齐 TS streamSimple 终态）。

    思考映射（对齐 TS）：不请求思考 → 显式关闭；adaptive 模型 → effort；
    旧模型 → 预算制（预算与 max_tokens 联合调整 + 窗口钳制）。
    """
    api_key = options.api_key if options else None
    headers = options.headers if options else None
    # 完整 federation 配置本身就是合法凭据来源（pi simple 入口同款豁免）；
    # 未命中时才走普通 key/header fail-fast 守卫。
    if (
        anthropic_federation_config(
            model, api_key, headers, options.env if options else None
        )
        is None
    ):
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
        # 预算与答案共享 max_tokens；若窗口连最小合法预算都容不下，
        # 响亮失败，绝不发 max_tokens=1/budget_tokens=1024 的非法组合。
        thinking_budget_tokens = min(adjusted_budget, max(0, max_tokens - 1024))
        if thinking_budget_tokens < 1024:
            raise ValueError(
                "Not enough context room for Anthropic thinking: "
                "budget_tokens must be at least 1024 and below max_tokens"
            )

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
        disable_continuation=base.disable_continuation,
        headers=base.headers,
        env=base.env,
        on_payload=base.on_payload,
        on_response=base.on_response,
        on_provider_stream_event=base.on_provider_stream_event,
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
