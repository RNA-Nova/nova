"""经服务端代理的模型流：线上事件校验、partial 重建与取消/断流收尾。"""

import codecs
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from nova_ai import (
    AssistantMessageEventStream,
    SimpleStreamOptions,
    StreamingJsonParser,
)
from nova_ai.utils.errors import StreamDisconnectedError, normalize_error
from nova_ai.utils.sanitize import REDACTED_SECRET, redact_secrets
from nova_async import AbortedError, AbortScope, is_aborted
from nova_protocol import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    Model,
    NovaBaseModel,
    NovaErrorKind,
    RedactedString,
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
)
from pydantic import Field, StrictInt, TypeAdapter


def _safe_proxy_url(url: str) -> str:
    """去掉 URL 中可能藏凭据或会话秘密的 userinfo/query/fragment。"""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


@dataclass(kw_only=True)
class ProxyStreamOptions(SimpleStreamOptions):
    """代理地址与本地取消信号；auth_token 只进请求头，不进请求体或 repr。"""

    proxy_url: str
    auth_token: RedactedString = field(repr=False)

    def __repr__(self) -> str:
        # 含密结构使用白名单 repr：URL 清洗，其余采样/元数据默认不展示。
        return f"ProxyStreamOptions(proxy_url={_safe_proxy_url(self.proxy_url)!r})"


class _ProxyHttpError(RuntimeError):
    """保留 HTTP 状态与响应的错误，供统一分类与 Retry-After 提取。"""

    def __init__(self, message: str, response: httpx.Response) -> None:
        super().__init__(message)
        self.status_code = response.status_code
        self.response = response


# 线上代理事件是跨进程输入，使用 role/type 判别联合逐项校验。
class _ProxyStart(NovaBaseModel):
    type: Literal["start"]


class _ProxyTextStart(NovaBaseModel):
    type: Literal["text_start"]
    content_index: StrictInt


class _ProxyTextDelta(NovaBaseModel):
    type: Literal["text_delta"]
    content_index: StrictInt
    delta: str


class _ProxyTextEnd(NovaBaseModel):
    type: Literal["text_end"]
    content_index: StrictInt
    content_signature: Optional[str] = None


class _ProxyThinkingStart(NovaBaseModel):
    type: Literal["thinking_start"]
    content_index: StrictInt


class _ProxyThinkingDelta(NovaBaseModel):
    type: Literal["thinking_delta"]
    content_index: StrictInt
    delta: str


class _ProxyThinkingEnd(NovaBaseModel):
    type: Literal["thinking_end"]
    content_index: StrictInt
    content_signature: Optional[str] = None


class _ProxyToolCallStart(NovaBaseModel):
    type: Literal["toolcall_start"]
    content_index: StrictInt
    id: str
    tool_name: str


class _ProxyToolCallDelta(NovaBaseModel):
    type: Literal["toolcall_delta"]
    content_index: StrictInt
    delta: str


class _ProxyToolCallEnd(NovaBaseModel):
    type: Literal["toolcall_end"]
    content_index: StrictInt
    tool_call: ToolCall


class _ProxyDone(NovaBaseModel):
    type: Literal["done"]
    reason: Literal["stop", "length", "toolUse"]
    usage: Usage
    provider_thinking_level: Optional[str] = None


class _ProxyError(NovaBaseModel):
    type: Literal["error"]
    reason: Literal["aborted", "error"]
    usage: Usage
    error_message: Optional[str] = None
    error_kind: Optional[NovaErrorKind] = None
    retry_after_ms: Optional[int] = None
    provider_thinking_level: Optional[str] = None


ProxyAssistantMessageEvent = Annotated[
    Union[
        _ProxyStart,
        _ProxyTextStart,
        _ProxyTextDelta,
        _ProxyTextEnd,
        _ProxyThinkingStart,
        _ProxyThinkingDelta,
        _ProxyThinkingEnd,
        _ProxyToolCallStart,
        _ProxyToolCallDelta,
        _ProxyToolCallEnd,
        _ProxyDone,
        _ProxyError,
    ],
    Field(discriminator="type"),
]
_PROXY_EVENT_ADAPTER = TypeAdapter(ProxyAssistantMessageEvent)
logger = logging.getLogger(__name__)


def _wire_value(value: Any) -> Any:
    """把请求选项中的模型/枚举递归转成纯 JSON；其余值原样保留。"""
    if isinstance(value, NovaBaseModel):
        return value.dump_wire(exclude_none=True)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _wire_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire_value(item) for item in value]
    return value


def _request_options(options: ProxyStreamOptions) -> Dict[str, Any]:
    """只发送 pi proxy 支持的序列化选项；auth/signal/钩子绝不上线。"""
    values = {
        "temperature": options.temperature,
        "samplingParams": options.sampling_params,
        "maxTokens": options.max_tokens,
        "reasoning": options.reasoning,
        "cacheRetention": options.cache_retention,
        "sessionId": options.session_id,
        "headers": options.headers,
        "metadata": options.metadata,
        "transport": options.transport,
        "thinkingBudgets": options.thinking_budgets,
        "maxRetryDelayMs": options.max_retry_delay_ms,
    }
    return {
        key: _wire_value(value) for key, value in values.items() if value is not None
    }


def _redact_proxy_text(value: Any, auth_token: str) -> str:
    """先精确遮盖本次 token，再跑通用 secret 词表。"""
    text = str(value)
    if auth_token:
        text = text.replace(str(auth_token), "[REDACTED_SECRET]")
    return redact_secrets(text)


def _normalize_proxy_error(
    partial: AssistantMessage,
    error: BaseException,
    auth_token: str,
    model: Model,
    *,
    preserve_message: bool = False,
) -> None:
    """归一错误分类与退避，并统一清洗上游或本地错误原文。"""
    normalized = normalize_error(error, domain="proxy", provider=model.provider)
    if partial.error_kind is None:
        partial.error_kind = normalized.kind
    if partial.retry_after_ms is None and normalized.retry_after_ms is not None:
        partial.retry_after_ms = int(normalized.retry_after_ms)
    message = (
        partial.error_message
        if preserve_message and partial.error_message
        else normalized.message
    )
    partial.error_message = _redact_proxy_text(message, auth_token)


def _set_block(partial: AssistantMessage, index: int, block: Any) -> None:
    """线上 contentIndex 只允许替换现有块或追加到下一位，拒绝负值/越界。"""
    if index < 0 or index > len(partial.content):
        raise ValueError(f"Invalid proxy contentIndex: {index}")
    if index == len(partial.content):
        partial.content.append(block)
    else:
        partial.content[index] = block


def _block_at(partial: AssistantMessage, index: int) -> Any:
    if index < 0 or index >= len(partial.content):
        raise ValueError(f"Invalid proxy contentIndex: {index}")
    return partial.content[index]


def _process_proxy_event(
    event: ProxyAssistantMessageEvent,
    partial: AssistantMessage,
    tool_arguments: Dict[int, StreamingJsonParser],
) -> Optional[Any]:
    """归一一个代理事件并更新 partial；未知类型只警告，不伪造内容。"""
    if isinstance(event, _ProxyStart):
        return StartEvent(partial=partial)
    if isinstance(event, _ProxyTextStart):
        block = TextContent()
        _set_block(partial, event.content_index, block)
        return TextStartEvent(content_index=event.content_index, partial=partial)
    if isinstance(event, _ProxyTextDelta):
        block = _block_at(partial, event.content_index)
        if not isinstance(block, TextContent):
            raise ValueError("Received text_delta for non-text content")
        block.text += event.delta
        return TextDeltaEvent(
            content_index=event.content_index, delta=event.delta, partial=partial
        )
    if isinstance(event, _ProxyTextEnd):
        block = _block_at(partial, event.content_index)
        if not isinstance(block, TextContent):
            raise ValueError("Received text_end for non-text content")
        block.text_signature = event.content_signature
        return TextEndEvent(
            content_index=event.content_index, content=block.text, partial=partial
        )
    if isinstance(event, _ProxyThinkingStart):
        block = ThinkingContent()
        _set_block(partial, event.content_index, block)
        return ThinkingStartEvent(content_index=event.content_index, partial=partial)
    if isinstance(event, _ProxyThinkingDelta):
        block = _block_at(partial, event.content_index)
        if not isinstance(block, ThinkingContent):
            raise ValueError("Received thinking_delta for non-thinking content")
        block.thinking += event.delta
        return ThinkingDeltaEvent(
            content_index=event.content_index, delta=event.delta, partial=partial
        )
    if isinstance(event, _ProxyThinkingEnd):
        block = _block_at(partial, event.content_index)
        if not isinstance(block, ThinkingContent):
            raise ValueError("Received thinking_end for non-thinking content")
        block.thinking_signature = event.content_signature
        return ThinkingEndEvent(
            content_index=event.content_index, content=block.thinking, partial=partial
        )
    if isinstance(event, _ProxyToolCallStart):
        block = ToolCall(id=event.id, name=event.tool_name, arguments={})
        _set_block(partial, event.content_index, block)
        tool_arguments[event.content_index] = StreamingJsonParser()
        return ToolCallStartEvent(content_index=event.content_index, partial=partial)
    if isinstance(event, _ProxyToolCallDelta):
        block = _block_at(partial, event.content_index)
        parser = tool_arguments.get(event.content_index)
        if not isinstance(block, ToolCall) or parser is None:
            raise ValueError("Received toolcall_delta for non-toolCall content")
        parser.feed(event.delta)
        if isinstance(parser.value, dict):
            block.arguments = parser.value
        return ToolCallDeltaEvent(
            content_index=event.content_index, delta=event.delta, partial=partial
        )
    if isinstance(event, _ProxyToolCallEnd):
        block = _block_at(partial, event.content_index)
        if not isinstance(block, ToolCall):
            raise ValueError(
                "Received toolcall_end for non-toolCall content at index "
                f"{event.content_index}"
            )
        # toolcall_end 可能只携带 namespace 等终态元数据；缺席的 arguments
        # 不得把 delta 已重建的参数覆盖成空对象。
        for name, value in event.tool_call.model_dump(exclude_unset=True).items():
            setattr(block, name, value)
        tool_arguments.pop(event.content_index, None)
        return ToolCallEndEvent(
            content_index=event.content_index, tool_call=block, partial=partial
        )
    if isinstance(event, _ProxyDone):
        partial.stop_reason = StopReason(event.reason)
        partial.usage = event.usage
        if event.provider_thinking_level is not None:
            partial.provider_thinking_level = event.provider_thinking_level
        # 上游 reason 已经过 Literal 校验；这里只收窄事件构造器的静态类型。
        return DoneEvent(reason=cast(Any, partial.stop_reason), message=partial)
    if isinstance(event, _ProxyError):
        partial.stop_reason = StopReason(event.reason)
        partial.error_message = event.error_message
        partial.error_kind = event.error_kind
        partial.retry_after_ms = event.retry_after_ms
        partial.usage = event.usage
        if event.provider_thinking_level is not None:
            partial.provider_thinking_level = event.provider_thinking_level
        return ErrorEvent(reason=cast(Any, partial.stop_reason), error=partial)
    logger.warning("Unhandled proxy event type: %s", getattr(event, "type", "unknown"))
    return None


def stream_proxy(
    model: Model, context: Context, options: ProxyStreamOptions
) -> AssistantMessageEventStream:
    """经 ``POST {proxy_url}/api/stream`` 代理模型请求并重建标准事件流。"""
    stream = AssistantMessageEventStream()

    async def _run() -> None:
        partial = AssistantMessage(
            role="assistant",
            stop_reason=StopReason.PENDING,
            content=[],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            timestamp=int(time.time() * 1000),
        )
        client = httpx.AsyncClient()
        response: Optional[httpx.Response] = None
        body_iterator: Optional[Any] = None
        tool_arguments: Dict[int, StreamingJsonParser] = {}
        buffer = ""
        decoder = codecs.getincrementaldecoder("utf-8")()
        saw_terminal = False

        def process_line(line: str) -> None:
            """处理一条 SSE；首个终态后调用方立即停止网络消费。"""
            nonlocal saw_terminal
            if not line.startswith("data: "):
                return
            data = line[6:].strip()
            if not data:
                return
            event = _process_proxy_event(
                _PROXY_EVENT_ADAPTER.validate_python(json.loads(data)),
                partial,
                tool_arguments,
            )
            if event is None:
                return
            if isinstance(event, ErrorEvent):
                if event.reason == StopReason.ABORTED:
                    partial.error_kind = None
                    partial.retry_after_ms = None
                    partial.error_message = _redact_proxy_text(
                        partial.error_message or "Request was aborted",
                        options.auth_token,
                    )
                else:
                    _normalize_proxy_error(
                        partial,
                        RuntimeError(
                            partial.error_message or "Proxy returned an error"
                        ),
                        options.auth_token,
                        model,
                        preserve_message=True,
                    )
                event = ErrorEvent(reason=event.reason, error=partial)
            if isinstance(event, (DoneEvent, ErrorEvent)):
                saw_terminal = True
            stream.push(event)

        try:
            abort_scope = AbortScope(options.signal)
            with abort_scope:
                response = await client.send(
                    client.build_request(
                        "POST",
                        f"{options.proxy_url.rstrip('/')}/api/stream",
                        headers={
                            "Authorization": f"Bearer {options.auth_token}",
                            "Content-Type": "application/json",
                        },
                        content=json.dumps(
                            {
                                "model": _wire_value(model),
                                "context": _wire_value(context),
                                "options": _request_options(options),
                            }
                        ),
                    ),
                    stream=True,
                )
                if not 200 <= response.status_code < 300:
                    await response.aread()
                    message = (
                        f"Proxy error: {response.status_code} {response.reason_phrase}"
                    )
                    try:
                        error_data = response.json()
                        if isinstance(error_data, dict) and isinstance(
                            error_data.get("error"), str
                        ):
                            message = f"Proxy error: {error_data['error']}"
                    except Exception:
                        pass
                    raise _ProxyHttpError(message, response)

                # 直接持有底层迭代器：response.aiter_bytes() 的包装层不透传
                # aclose，终态退出必须能真实关闭服务端流。
                if not isinstance(response.stream, httpx.AsyncByteStream):
                    raise TypeError("Proxy requires an asynchronous response stream")
                body_iterator = response.stream.__aiter__()
                async for chunk in body_iterator:
                    buffer += decoder.decode(chunk)
                    lines = buffer.split("\n")
                    buffer = lines.pop() or ""
                    for line in lines:
                        process_line(line)
                        if saw_terminal:
                            break
                    if saw_terminal:
                        break
                if saw_terminal:
                    return

                # 最后一条事件可以没有换行；先冲刷增量 UTF-8 解码器与 buffer。
                buffer += decoder.decode(b"", final=True)
                if buffer:
                    process_line(buffer.strip())
                if saw_terminal:
                    return
                raise StreamDisconnectedError(
                    "Connection closed by proxy server before the response completed"
                )
            # scope 在块边界吞掉自家取消；归因必须在块后翻译。
            abort_scope.raise_if_aborted()
        except Exception as error:
            if stream.done:
                return
            aborted = is_aborted(options.signal) or isinstance(error, AbortedError)
            partial.stop_reason = StopReason.ABORTED if aborted else StopReason.ERROR
            if aborted:
                partial.error_kind = None
                partial.retry_after_ms = None
                partial.error_message = _redact_proxy_text(
                    str(error) or "Request was aborted", options.auth_token
                )
            else:
                _normalize_proxy_error(partial, error, options.auth_token, model)
            stream.push(ErrorEvent(reason=partial.stop_reason, error=partial))
        finally:
            if body_iterator is not None:
                try:
                    await body_iterator.aclose()
                except Exception:
                    pass
            if response is not None:
                try:
                    await response.aclose()
                except Exception:
                    pass
            try:
                await client.aclose()
            except Exception:
                pass

    stream.drive(_run())
    return stream


__all__ = [
    "ProxyAssistantMessageEvent",
    "ProxyStreamOptions",
    "stream_proxy",
]
