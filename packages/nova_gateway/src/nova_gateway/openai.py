"""OpenAI Chat Completions 与 Nova 原生请求/事件的互转。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union

from nova_ai.utils.sanitize import redact_secrets
from nova_gateway_protocol import (
    GatewayAssistantMessageEvent,
    GatewayDone,
    GatewayError,
    GatewayStart,
    GatewayTextDelta,
    GatewayTextEnd,
    GatewayTextStart,
    GatewayThinkingDelta,
    GatewayThinkingEnd,
    GatewayThinkingStart,
    GatewayToolCallDelta,
    GatewayToolCallEnd,
    GatewayToolCallStart,
    ModelRef,
    StreamOptions,
    StreamRequest,
)
from nova_protocol import (
    AssistantMessage,
    Context,
    Model,
    NovaBaseModel,
    StopReason,
    SystemMessage,
    TextContent,
    ThinkingLevel,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from pydantic import ConfigDict


class OpenAIError(ValueError):
    """OpenAI 兼容请求拒绝。"""


class OpenAIStreamOptions(NovaBaseModel):
    """流式附加选项；`include_usage` 缺省时末块不携带 usage。"""

    model_config = ConfigDict(extra="forbid")
    include_usage: bool = False


class OpenAIChatRequest(NovaBaseModel):
    """受支持的 Chat Completions 子集；可选项缺席时走 Provider 缺省语义。"""

    model_config = ConfigDict(extra="forbid")
    model: str
    messages: List[Dict[str, Any]]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    stream: bool = False
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    top_p: Optional[float] = None
    stop: Optional[Union[str, List[str]]] = None
    seed: Optional[int] = None
    reasoning_effort: Optional[str] = None
    response_format: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    stream_options: Optional[OpenAIStreamOptions] = None


def _text_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for entry in value:
            if not isinstance(entry, dict) or entry.get("type") != "text":
                raise OpenAIError("Only text content parts are supported")
            parts.append(entry.get("text", ""))
        return "".join(parts)
    raise OpenAIError(f"Unsupported message content shape: {type(value).__name__}")


def _tool_calls(values: Any) -> list[ToolCall]:
    result = []
    for entry in values or []:
        if not isinstance(entry, dict) or entry.get("type") != "function":
            raise OpenAIError("Only function tool calls are supported")
        function = entry.get("function") or {}
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError as error:
                raise OpenAIError("Invalid tool call arguments JSON") from error
        if not isinstance(arguments, dict):
            raise OpenAIError("Tool call arguments must be a JSON object")
        result.append(
            ToolCall(
                id=entry.get("id") or "",
                name=function.get("name") or "",
                arguments=arguments,
            )
        )
    return result


def _messages(values: List[Dict[str, Any]]) -> list[Any]:
    result = []
    for value in values:
        role = value.get("role")
        content = _text_content(value.get("content"))
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "user":
            result.append(UserMessage(content=content))
        elif role == "assistant":
            calls = _tool_calls(value.get("tool_calls"))
            content_blocks: list[Any] = []
            if content:
                content_blocks.append(TextContent(text=content))
            content_blocks.extend(calls)
            result.append(
                AssistantMessage(
                    content=content_blocks,
                    stop_reason=StopReason.TOOL_USE if calls else StopReason.STOP,
                )
            )
        elif role == "tool":
            result.append(
                ToolResultMessage(
                    tool_call_id=value.get("tool_call_id") or "",
                    tool_name=value.get("name") or "",
                    content=[TextContent(text=content)],
                    is_error=bool(value.get("is_error", False)),
                )
            )
        else:
            raise OpenAIError(f"Unsupported message role: {role}")
    return result


def _tools(values: Optional[List[Dict[str, Any]]]) -> Optional[List[Tool]]:
    if values is None:
        return None
    result = []
    for value in values:
        if value.get("type") != "function":
            raise OpenAIError("Only function tools are supported")
        function = value.get("function") or {}
        parameters = function.get("parameters", {})
        if not isinstance(parameters, dict):
            raise OpenAIError("Tool parameters must be a JSON Schema object")
        result.append(
            Tool(
                name=function.get("name") or "",
                description=function.get("description") or "",
                parameters=parameters,
            )
        )
    return result


def to_native_request(request: OpenAIChatRequest) -> StreamRequest:
    if "/" not in request.model:
        raise OpenAIError("Model must use provider/model_id form")
    if request.max_tokens is not None and request.max_completion_tokens is not None:
        raise OpenAIError("Use only one of max_tokens and max_completion_tokens")
    if request.response_format is not None:
        raise OpenAIError("response_format is not supported")
    provider, model_id = request.model.split("/", 1)
    options = StreamOptions(
        temperature=request.temperature,
        max_tokens=(
            request.max_completion_tokens
            if request.max_completion_tokens is not None
            else request.max_tokens
        ),
        reasoning=_thinking_level(request.reasoning_effort),
        metadata=request.metadata,
    )
    sampling: Dict[str, Any] = {}
    if request.top_p is not None:
        sampling["top_p"] = request.top_p
    if request.stop is not None:
        sampling["stop"] = request.stop
    if request.seed is not None:
        sampling["seed"] = request.seed
    if sampling:
        options.sampling_params = sampling
    options.tool_choice = request.tool_choice
    return StreamRequest(
        model=ModelRef(provider=provider, id=model_id),
        context=Context(
            messages=_messages(request.messages), tools=_tools(request.tools)
        ),
        options=options,
    )


def _thinking_level(value: Optional[str]) -> Optional[ThinkingLevel]:
    if value is None:
        return None
    try:
        return ThinkingLevel(value)
    except ValueError as error:
        raise OpenAIError(f"Unsupported reasoning_effort: {value}") from error


def _finish_reason(reason: StopReason) -> str:
    if reason == StopReason.LENGTH:
        return "length"
    if reason == StopReason.TOOL_USE:
        return "tool_calls"
    if reason == StopReason.ERROR:
        return "error"
    return "stop"


def _usage(usage: Usage) -> dict[str, int]:
    return {
        "prompt_tokens": usage.input,
        "completion_tokens": usage.output,
        "total_tokens": usage.total_tokens,
    }


def to_openai_response(
    message: AssistantMessage, request: OpenAIChatRequest
) -> dict[str, Any]:
    text = "".join(
        block.text for block in message.content if isinstance(block, TextContent)
    )
    calls = [block for block in message.content if isinstance(block, ToolCall)]
    return {
        "id": message.response_id or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": (
            message.timestamp // 1000 if message.timestamp else int(time.time())
        ),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                    **(
                        {"tool_calls": [_openai_tool_call(call) for call in calls]}
                        if calls
                        else {}
                    ),
                },
                "finish_reason": _finish_reason(message.stop_reason),
            }
        ],
        "usage": _usage(message.usage),
    }


def _openai_tool_call(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments, ensure_ascii=False),
        },
    }


@dataclass(frozen=True, kw_only=True)
class OpenAIStreamContext:
    id: str
    created: int
    model: str
    include_usage: bool


def new_stream_context(request: OpenAIChatRequest) -> OpenAIStreamContext:
    return OpenAIStreamContext(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        created=int(time.time() * 1000) // 1000,
        model=request.model,
        include_usage=bool(
            request.stream_options and request.stream_options.include_usage
        ),
    )


def to_openai_chunk(
    event: GatewayAssistantMessageEvent,
    context: OpenAIStreamContext,
) -> Optional[dict[str, Any]]:
    delta: Dict[str, Any] = {}
    finish_reason = None
    usage = None
    if isinstance(event, GatewayStart):
        delta = {"role": "assistant", "content": ""}
    elif isinstance(event, GatewayTextDelta):
        delta = {"content": event.delta}
    elif isinstance(event, GatewayTextEnd):
        return None
    elif isinstance(event, GatewayThinkingDelta):
        delta = {"reasoning_content": event.delta}
    elif isinstance(event, GatewayThinkingStart):
        delta = {"reasoning_content": ""}
    elif isinstance(event, GatewayThinkingEnd):
        return None
    elif isinstance(event, GatewayToolCallStart):
        delta = {
            "tool_calls": [
                {
                    "index": event.content_index,
                    "id": event.id,
                    "type": "function",
                    "function": {"name": event.tool_name, "arguments": ""},
                }
            ]
        }
    elif isinstance(event, GatewayToolCallDelta):
        delta = {
            "tool_calls": [
                {
                    "index": event.content_index,
                    "function": {"arguments": event.delta},
                }
            ]
        }
    elif isinstance(event, GatewayToolCallEnd):
        delta = {
            "tool_calls": [
                {"index": event.content_index, "function": {"arguments": ""}}
            ]
        }
    elif isinstance(event, GatewayDone):
        finish_reason = _finish_reason(StopReason(event.reason))
        usage = _usage(event.usage) if context.include_usage else None
    elif isinstance(event, GatewayError):
        finish_reason = _finish_reason(StopReason(event.reason))
        delta = {"content": event.error_message or ""}
        usage = _usage(event.usage) if context.include_usage else None
    else:
        return None
    choice: Dict[str, Any] = {
        "index": 0,
        "delta": delta,
        "finish_reason": finish_reason,
    }
    return {
        "id": context.id,
        "object": "chat.completion.chunk",
        "created": context.created,
        "model": context.model,
        "choices": [choice],
        **({"usage": usage} if usage is not None else {}),
    }


def openai_error_payload(
    error: OpenAIError | Exception, kind: str = "invalid_request_error"
) -> dict[str, Any]:
    return {
        "error": {
            "message": redact_secrets(str(error)),
            "type": kind,
            "param": None,
            "code": None,
        }
    }


__all__ = [
    "OpenAIChatRequest",
    "OpenAIError",
    "OpenAIStreamContext",
    "OpenAIStreamOptions",
    "new_stream_context",
    "openai_error_payload",
    "to_native_request",
    "to_openai_chunk",
    "to_openai_response",
]
