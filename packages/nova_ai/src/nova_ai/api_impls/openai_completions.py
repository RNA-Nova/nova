"""
OpenAI Completions API 流式处理实现
"""

import inspect
import json
import re
import time
from typing import List, Dict, Optional, Any, Union, Tuple
import asyncio
from copy import deepcopy


import openai
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionAssistantMessageParam,
    ChatCompletionToolMessageParam,
)

from ..types.enums import StopReason, KnownApi
from ..types.messages import Message, AssistantMessage, Context, Tool
from ..types.content import (
    TextContent,
    ThinkingContent,
    ToolCall,
)
from ..types.model import Model
from ..types.model import Usage, Cost
from ..utils.env import get_env_api_key
from ..utils.json_parser import parse_streaming_json
from ..utils.surrogate import sanitize_surrogates
from ..utils.copilot import has_copilot_vision_input, build_copilot_dynamic_headers
from ..types.stream_options import StreamOptions, SimpleStreamOptions, ProviderResponse
from ..utils.stream_options import build_base_options, clamp_reasoning
from ..utils.message_transformer import transform_messages
from ..utils import calculate_cost, supports_xhigh_thinking
from ..types.events import (
    StartEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    DoneEvent,
    ErrorEvent,
)
from ..streaming import AssistantMessageEventStream
from ..types.compat import OpenAICompletionsCompat


class OpenAICompletionsOptions(StreamOptions):
    """OpenAI Completions 特定选项"""

    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    reasoning_effort: Optional[str] = None
    parallel_tool_calls: Optional[bool] = None


def normalize_mistral_tool_id(id: str) -> str:
    """
    规范化工具调用ID以适应Mistral

    Mistral要求工具ID正好是9个字母数字字符（a-z, A-Z, 0-9）
    """
    # 移除非字母数字字符
    normalized = "".join(c for c in id if c.isalnum())

    # Mistral要求正好9个字符
    if len(normalized) < 9:
        # 基于原始ID使用确定性字符填充以确保匹配
        padding = "ABCDEFGHI"
        normalized = normalized + padding[: 9 - len(normalized)]
    elif len(normalized) > 9:
        normalized = normalized[:9]

    return normalized


def has_tool_history(messages: List[Message]) -> bool:
    """
    检查对话消息是否包含工具调用或工具结果

    因为Anthropic（通过代理）要求在消息包含tool_calls或tool角色消息时
    必须提供tools参数
    """
    for msg in messages:
        if msg.role == "toolResult":
            return True
        if msg.role == "assistant":
            if any(block.type == "toolCall" for block in msg.content):
                return True
    return False


def map_stop_reason(reason: Optional[str]) -> Tuple[StopReason, Optional[str]]:
    """映射OpenAI的finish_reason到标准StopReason

    返回 (stop_reason, error_message)，其中 error_message 在异常 finish_reason 时提供。
    """
    if reason is None:
        return StopReason.STOP, None

    if reason in ("stop", "end"):
        return StopReason.STOP, None
    elif reason == "length":
        return StopReason.LENGTH, None
    elif reason in ("function_call", "tool_calls"):
        return StopReason.TOOL_USE, None
    elif reason == "content_filter":
        return StopReason.ERROR, "Provider finish_reason: content_filter"
    elif reason == "network_error":
        return StopReason.ERROR, "Provider finish_reason: network_error"
    else:
        return StopReason.ERROR, f"Provider finish_reason: {reason}"


def detect_compat(model: Model) -> OpenAICompletionsCompat:
    """
    从提供商和baseUrl检测兼容性设置

    提供商优先于基于URL的检测，因为它是显式配置的
    """
    provider = model.provider
    base_url = model.base_url

    is_zai = provider == "zai" or "api.z.ai" in base_url
    is_together = (
        provider == "together"
        or "api.together.ai" in base_url
        or "api.together.xyz" in base_url
    )
    is_moonshot = (
        provider == "moonshotai"
        or provider == "moonshotai-cn"
        or "api.moonshot." in base_url
    )
    is_cloudflare_workers_ai = (
        provider == "cloudflare-workers-ai" or "api.cloudflare.com" in base_url
    )
    is_cloudflare_ai_gateway = (
        provider == "cloudflare-ai-gateway" or "gateway.ai.cloudflare.com" in base_url
    )

    is_non_standard = (
        provider == "volcengine"
        or "volces.com" in base_url
        or "googleapis.com" in base_url
        or provider == "cerebras"
        or "cerebras.ai" in base_url
        or provider == "xai"
        or "api.x.ai" in base_url
        or provider == "mistral"
        or "mistral.ai" in base_url
        or "chutes.ai" in base_url
        or "deepseek.com" in base_url
        or is_zai
        or provider == "opencode"
        or "opencode.ai" in base_url
        or is_together
        or is_moonshot
        or is_cloudflare_workers_ai
        or is_cloudflare_ai_gateway
    )

    use_max_tokens = (
        provider == "mistral"
        or "mistral.ai" in base_url
        or "chutes.ai" in base_url
        or is_moonshot
        or is_cloudflare_ai_gateway
        or is_together
    )

    is_grok = provider == "xai" or "api.x.ai" in base_url
    is_deepseek = (
        provider == "deepseek"
        or "deepseek.com" in base_url
        or (provider == "volcengine" and model.id.startswith("deepseek"))
    )
    is_mistral = provider == "mistral" or "mistral.ai" in base_url

    cache_control_format = (
        "anthropic"
        if (provider == "openrouter" and model.id.startswith("anthropic/"))
        else None
    )

    thinking_format = "openai"
    if is_deepseek:
        thinking_format = "deepseek"
    elif is_zai:
        thinking_format = "zai"
    elif is_together:
        thinking_format = "together"
    elif provider == "openrouter" or "openrouter.ai" in base_url:
        thinking_format = "openrouter"

    return OpenAICompletionsCompat(
        supports_store=not is_non_standard,
        supports_developer_role=not is_non_standard,
        supports_reasoning_effort=not (
            is_grok or is_zai or is_moonshot or is_together or is_cloudflare_ai_gateway
        ),
        supports_usage_in_streaming=True,
        max_tokens_field="max_tokens" if use_max_tokens else "max_completion_tokens",
        requires_tool_result_name=is_mistral,
        requires_assistant_after_tool_result=False,
        requires_thinking_as_text=is_mistral,
        requires_mistral_tool_ids=is_mistral,
        requires_reasoning_content_on_assistant_messages=is_deepseek,
        thinking_format=thinking_format,
        open_router_routing={},
        vercel_gateway_routing={},
        zai_tool_stream=False,
        supports_strict_mode=not (
            is_moonshot or is_together or is_cloudflare_ai_gateway
        ),
        cache_control_format=cache_control_format,
        send_session_affinity_headers=False,
        supports_long_cache_retention=not (
            is_together or is_cloudflare_workers_ai or is_cloudflare_ai_gateway
        ),
    )


def get_compat(model: Model) -> OpenAICompletionsCompat:
    """
    获取模型的解析后兼容性设置

    如果提供了model.compat则使用，否则自动检测。
    若model.compat为OpenAIResponsesCompat（不适用于completions API），则忽略并返回自动检测结果。
    """
    detected = detect_compat(model)
    if model.compat is None:
        return detected

    # model.compat 可能是 OpenAIResponsesCompat，不适用于 completions API
    if not isinstance(model.compat, OpenAICompletionsCompat):
        return detected

    compat = model.compat

    return OpenAICompletionsCompat(
        supports_store=(
            compat.supports_store
            if compat.supports_store is not None
            else detected.supports_store
        ),
        supports_developer_role=(
            compat.supports_developer_role
            if compat.supports_developer_role is not None
            else detected.supports_developer_role
        ),
        supports_reasoning_effort=(
            compat.supports_reasoning_effort
            if compat.supports_reasoning_effort is not None
            else detected.supports_reasoning_effort
        ),
        supports_usage_in_streaming=(
            compat.supports_usage_in_streaming
            if compat.supports_usage_in_streaming is not None
            else detected.supports_usage_in_streaming
        ),
        max_tokens_field=compat.max_tokens_field or detected.max_tokens_field,
        requires_tool_result_name=(
            compat.requires_tool_result_name
            if compat.requires_tool_result_name is not None
            else detected.requires_tool_result_name
        ),
        requires_assistant_after_tool_result=(
            compat.requires_assistant_after_tool_result
            if compat.requires_assistant_after_tool_result is not None
            else detected.requires_assistant_after_tool_result
        ),
        requires_thinking_as_text=(
            compat.requires_thinking_as_text
            if compat.requires_thinking_as_text is not None
            else detected.requires_thinking_as_text
        ),
        requires_mistral_tool_ids=(
            compat.requires_mistral_tool_ids
            if compat.requires_mistral_tool_ids is not None
            else detected.requires_mistral_tool_ids
        ),
        requires_reasoning_content_on_assistant_messages=(
            compat.requires_reasoning_content_on_assistant_messages
            if compat.requires_reasoning_content_on_assistant_messages is not None
            else detected.requires_reasoning_content_on_assistant_messages
        ),
        thinking_format=compat.thinking_format or detected.thinking_format,
        open_router_routing=compat.open_router_routing or detected.open_router_routing,
        vercel_gateway_routing=compat.vercel_gateway_routing
        or detected.vercel_gateway_routing,
        zai_tool_stream=(
            compat.zai_tool_stream
            if compat.zai_tool_stream is not None
            else detected.zai_tool_stream
        ),
        supports_strict_mode=(
            compat.supports_strict_mode
            if compat.supports_strict_mode is not None
            else detected.supports_strict_mode
        ),
        cache_control_format=compat.cache_control_format
        or detected.cache_control_format,
        send_session_affinity_headers=(
            compat.send_session_affinity_headers
            if compat.send_session_affinity_headers is not None
            else detected.send_session_affinity_headers
        ),
        supports_long_cache_retention=(
            compat.supports_long_cache_retention
            if compat.supports_long_cache_retention is not None
            else detected.supports_long_cache_retention
        ),
    )


def maybe_add_openrouter_anthropic_cache_control(
    model: Model, messages: List[ChatCompletionMessageParam]
) -> None:
    """为OpenRouter上的Anthropic模型添加缓存控制"""
    if model.provider != "openrouter" or not model.id.startswith("anthropic/"):
        return

    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if msg["role"] not in ["user", "assistant"]:
            continue

        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            return

        if not isinstance(content, list):
            continue

        for j in range(len(content) - 1, -1, -1):
            part = content[j]
            if isinstance(part, dict) and part.get("type") == "text":
                part["cache_control"] = {"type": "ephemeral"}
                return


def convert_messages(
    model: Model, context: Context, compat: OpenAICompletionsCompat
) -> List[ChatCompletionMessageParam]:
    """将标准消息转换为OpenAI格式"""
    params: List[ChatCompletionMessageParam] = []

    def normalize_tool_call_id(id: str) -> str:
        """规范化工具调用ID"""
        if compat.requires_mistral_tool_ids:
            return normalize_mistral_tool_id(id)

        if "|" in id:
            call_id = id.split("|")[0]
            return re.sub(r"[^a-zA-Z0-9_-]", "_", call_id)[:40]

        if model.provider == "openai":
            return id[:40] if len(id) > 40 else id
        return id

    transformed_messages = transform_messages(
        context.messages, model, lambda id, m, src: normalize_tool_call_id(id)
    )

    if context.system_prompt:
        use_developer_role = model.reasoning and compat.supports_developer_role
        role = "developer" if use_developer_role else "system"
        params.append(
            {"role": role, "content": sanitize_surrogates(context.system_prompt)}
        )

    last_role: Optional[str] = None

    i = 0
    while i < len(transformed_messages):
        msg = transformed_messages[i]

        if (
            compat.requires_assistant_after_tool_result
            and last_role == "toolResult"
            and msg.role == "user"
        ):
            params.append(
                {"role": "assistant", "content": "I have processed the tool results."}
            )

        if msg.role == "user":
            user_msg = msg
            if isinstance(user_msg.content, str):
                params.append(
                    {"role": "user", "content": sanitize_surrogates(user_msg.content)}
                )
            else:
                content = []
                for item in user_msg.content:
                    if item.type == "text":
                        content.append(
                            {"type": "text", "text": sanitize_surrogates(item.text)}
                        )
                    elif item.type == "image":
                        content.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{item.mime_type};base64,{item.data}"
                                },
                            }
                        )
                if "image" not in model.input_types:
                    content = [c for c in content if c.get("type") != "image_url"]

                if not content:
                    # 如果过滤后内容为空（例如纯图片消息但模型不支持图片），
                    # 至少保留一个占位文本，避免消息完全丢失破坏角色交替
                    content = [{"type": "text", "text": "(image)"}]

                params.append({"role": "user", "content": content})

        elif msg.role == "assistant":
            assistant_msg = msg

            assistant_param: ChatCompletionAssistantMessageParam = {
                "role": "assistant",
                "content": "" if compat.requires_assistant_after_tool_result else None,
            }

            text_blocks = [b for b in assistant_msg.content if b.type == "text"]
            non_empty_text = [b for b in text_blocks if b.text and b.text.strip()]
            assistant_text_parts = [
                {"type": "text", "text": sanitize_surrogates(b.text)}
                for b in non_empty_text
            ]
            assistant_text = "".join(p["text"] for p in assistant_text_parts)

            thinking_blocks = [b for b in assistant_msg.content if b.type == "thinking"]
            non_empty_thinking = [
                b for b in thinking_blocks if b.thinking and b.thinking.strip()
            ]

            if non_empty_thinking:
                if compat.requires_thinking_as_text:
                    # 将思考块转换为纯文本（不带标签，避免模型模仿）
                    thinking_text = "\n\n".join(
                        sanitize_surrogates(b.thinking) for b in non_empty_thinking
                    )
                    assistant_param["content"] = [
                        {"type": "text", "text": thinking_text},
                        *assistant_text_parts,
                    ]
                else:
                    # 始终将助手内容作为纯字符串发送（OpenAI Chat Completions API 标准格式）
                    if assistant_text:
                        assistant_param["content"] = assistant_text

                    # 使用第一个思考块的签名（用于 llama.cpp server + gpt-oss）
                    signature = non_empty_thinking[0].thinking_signature
                    if signature and len(signature) > 0:
                        assistant_param[signature] = "\n".join(
                            b.thinking for b in non_empty_thinking
                        )
            elif assistant_text:
                # 始终将助手内容作为纯字符串发送（OpenAI Chat Completions API 标准格式）
                assistant_param["content"] = assistant_text

            tool_calls = [b for b in assistant_msg.content if b.type == "toolCall"]
            if tool_calls:
                assistant_param["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ]

                reasoning_details = []
                for tc in tool_calls:
                    if tc.thought_signature:
                        try:
                            reasoning_details.append(json.loads(tc.thought_signature))
                        except Exception:
                            pass
                if reasoning_details:
                    assistant_param["reasoning_details"] = reasoning_details

            # DeepSeek 要求 assistant 消息上有 reasoning_content 字段
            if (
                compat.requires_reasoning_content_on_assistant_messages
                and model.reasoning
                and assistant_param.get("reasoning_content") is None
            ):
                assistant_param["reasoning_content"] = ""

            content = assistant_param.get("content")
            has_content = content is not None and (
                (isinstance(content, str) and len(content) > 0)
                or (isinstance(content, list) and len(content) > 0)
            )
            if not has_content and "tool_calls" not in assistant_param:
                i += 1
                continue

            params.append(assistant_param)

        elif msg.role == "toolResult":
            image_blocks = []

            j = i
            while (
                j < len(transformed_messages)
                and transformed_messages[j].role == "toolResult"
            ):
                curr = transformed_messages[j]

                text_result = "\n".join(
                    c.text for c in curr.content if c.type == "text"
                )
                has_images = any(c.type == "image" for c in curr.content)

                has_text = len(text_result) > 0
                tool_result_param: ChatCompletionToolMessageParam = {
                    "role": "tool",
                    "content": sanitize_surrogates(
                        text_result if has_text else "(see attached image)"
                    ),
                    "tool_call_id": curr.tool_call_id,
                }
                if compat.requires_tool_result_name and curr.tool_name:
                    tool_result_param.name = curr.tool_name

                params.append(tool_result_param)

                if has_images and "image" in model.input_types:
                    for block in curr.content:
                        if block.type == "image":
                            image_blocks.append(
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{block.mime_type};base64,{block.data}"
                                    },
                                }
                            )

                j += 1

            i = j - 1

            if image_blocks:
                if compat.requires_assistant_after_tool_result:
                    params.append(
                        {
                            "role": "assistant",
                            "content": "I have processed the tool results.",
                        }
                    )

                params.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Attached image(s) from tool result:",
                            },
                            *image_blocks,
                        ],
                    }
                )
                last_role = "user"
            else:
                last_role = "toolResult"

            i += 1
            continue

        last_role = msg.role
        i += 1

    return params


def convert_tools(
    tools: List[Tool], compat: OpenAICompletionsCompat
) -> List[Dict[str, Any]]:
    """转换工具定义为OpenAI格式"""
    result = []
    for tool in tools:
        tool_dict = {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        if compat.supports_strict_mode is not False:
            tool_dict["function"]["strict"] = False

        result.append(tool_dict)

    return result


def create_client(
    model: Model,
    context: Context,
    api_key: Optional[str] = None,
    options_headers: Optional[Dict[str, str]] = None,
    session_id: Optional[str] = None,
    compat: Optional[OpenAICompletionsCompat] = None,
    max_retries: Optional[int] = None,
) -> AsyncOpenAI:
    """创建OpenAI客户端"""
    if not api_key:
        api_key = get_env_api_key(model.provider)
        if not api_key:
            raise ValueError(
                "OpenAI API key is required. Set OPENAI_API_KEY environment variable "
                "or pass it as an argument."
            )

    headers = {}
    if model.headers:
        headers.update(model.headers)

    if model.provider == "github-copilot":
        has_images = has_copilot_vision_input(context.messages)
        copilot_headers = build_copilot_dynamic_headers(context.messages, has_images)
        headers.update(copilot_headers)

    resolved_compat = compat or get_compat(model)
    if session_id and resolved_compat.send_session_affinity_headers:
        headers["session_id"] = session_id
        headers["x-client-request-id"] = session_id
        headers["x-session-affinity"] = session_id

    if options_headers:
        headers.update(options_headers)

    # Cloudflare AI Gateway 特殊鉴权头部
    if model.provider == "cloudflare-ai-gateway":
        default_headers = {
            **headers,
            "Authorization": headers.get("Authorization") or "",
            "cf-aig-authorization": f"Bearer {api_key}",
        }
    else:
        default_headers = headers

    return AsyncOpenAI(
        api_key=api_key,
        base_url=model.base_url,
        default_headers=default_headers,
        max_retries=max_retries if max_retries is not None else 2,
    )


def _resolve_cache_retention(options: Optional[OpenAICompletionsOptions]) -> str:
    """解析缓存保留策略"""
    if options and options.cache_retention:
        return options.cache_retention
    import os

    if os.environ.get("PI_CACHE_RETENTION") == "long":
        return "long"
    return "short"


def build_params(
    model: Model,
    context: Context,
    options: Optional[OpenAICompletionsOptions] = None,
    compat: Optional[OpenAICompletionsCompat] = None,
    cache_retention: Optional[str] = None,
) -> Dict[str, Any]:
    """构建OpenAI API请求体参数

    所有与请求体相关的参数（包括消息转换、缓存控制、工具、推理等）
    都集中在此函数内处理。调用层负责传入已解析的 compat 和 cache_retention，
    避免重复计算。
    """
    resolved_compat = compat or get_compat(model)
    resolved_cache_retention = cache_retention or _resolve_cache_retention(options)

    # 函数签名中的 compat/cache_retention 已由调用层解析，避免重复计算。
    # 下面统一用简短名称 compat/cache_retention 指代解析后的值。
    compat = resolved_compat
    cache_retention = resolved_cache_retention

    messages = convert_messages(model, context, compat)
    maybe_add_openrouter_anthropic_cache_control(model, messages)

    reasoning_effort = options.reasoning_effort if options else None
    enabled = bool(reasoning_effort and reasoning_effort != "off")

    def _map_reasoning_effort(effort: Optional[str]) -> Optional[str]:
        """通过 thinking_level_map 映射 reasoning_effort"""
        if effort and model.thinking_level_map and effort in model.thinking_level_map:
            mapped = model.thinking_level_map[effort]
            if mapped is not None:
                return mapped
        return effort

    def _get_reasoning_effort() -> Optional[str]:
        """获取最终应发送的 reasoning_effort 值"""
        mapped = _map_reasoning_effort(reasoning_effort)
        if mapped:
            return mapped
        off = _map_reasoning_effort("off")
        return off if isinstance(off, str) else None

    final_reasoning_effort = _get_reasoning_effort()

    use_prompt_cache_key = (
        options
        and options.session_id
        and (
            ("api.openai.com" in model.base_url and cache_retention != "none")
            or (cache_retention == "long" and compat.supports_long_cache_retention)
        )
    )
    use_prompt_cache_retention = (
        cache_retention == "long" and compat.supports_long_cache_retention
    )

    params: Dict[str, Any] = {
        "model": model.id,
        "messages": messages,
        "stream": True,
        "prompt_cache_key": options.session_id if use_prompt_cache_key else None,
        "prompt_cache_retention": "24h" if use_prompt_cache_retention else None,
    }

    # 所有 provider-specific / 非标准字段都通过 extra_body 传递
    extra_body: Dict[str, Any] = {}

    if compat.supports_usage_in_streaming is not False:
        params["stream_options"] = {"include_usage": True}

    if compat.supports_store:
        params["store"] = False

    if options and options.metadata:
        params["metadata"] = options.metadata

    if options and options.max_tokens:
        if compat.max_tokens_field == "max_tokens":
            params["max_tokens"] = options.max_tokens
        else:
            params["max_completion_tokens"] = options.max_tokens

    if options and options.temperature is not None:
        params["temperature"] = options.temperature

    if context.tools:
        params["tools"] = convert_tools(context.tools, compat)
        if compat.zai_tool_stream:
            extra_body["tool_stream"] = True
        if options and options.parallel_tool_calls is not None:
            params["parallel_tool_calls"] = options.parallel_tool_calls
    elif has_tool_history(context.messages):
        params["tools"] = []

    if options and options.tool_choice:
        params["tool_choice"] = options.tool_choice

    # 推理/思考参数格式处理
    # 注意：所有 provider-specific 的 thinking 参数都通过 extra_body 传递，
    # 因为 OpenAI Python SDK 不直接支持这些关键字参数。
    if model.reasoning:
        if compat.thinking_format == "zai":
            extra_body["enable_thinking"] = enabled
        elif compat.thinking_format == "qwen":
            extra_body["enable_thinking"] = enabled
        elif compat.thinking_format == "qwen-chat-template":
            extra_body["chat_template_kwargs"] = {
                "enable_thinking": enabled,
                "preserve_thinking": True,
            }
        elif compat.thinking_format == "deepseek":
            extra_body["thinking"] = {"type": "enabled" if enabled else "disabled"}
            if enabled:
                params["reasoning_effort"] = final_reasoning_effort
        elif compat.thinking_format == "openrouter":
            extra_body["reasoning"] = {"effort": final_reasoning_effort or "none"}
        elif compat.thinking_format == "together":
            extra_body["reasoning"] = {"enabled": enabled}
            if enabled and compat.supports_reasoning_effort:
                params["reasoning_effort"] = final_reasoning_effort
        elif compat.supports_reasoning_effort:
            params["reasoning_effort"] = final_reasoning_effort

    if "openrouter.ai" in model.base_url and compat.open_router_routing:
        extra_body["provider"] = compat.open_router_routing.model_dump()

    if "ai-gateway.vercel.sh" in model.base_url and compat.vercel_gateway_routing:
        routing = compat.vercel_gateway_routing
        if routing.only or routing.order:
            gateway_options: Dict[str, Any] = {}
            if routing.only:
                gateway_options["only"] = routing.only
            if routing.order:
                gateway_options["order"] = routing.order
            extra_body["providerOptions"] = {"gateway": gateway_options}

    # 清理 None 值，避免把 null 传给 SDK
    params = {k: v for k, v in params.items() if v is not None}

    if extra_body:
        params["extra_body"] = extra_body

    return params


def _apply_chunk_usage(output: AssistantMessage, usage: Any, model: Model) -> None:
    """将单个 chunk 的 usage 信息应用到输出消息上"""
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0

    prompt_tokens_details = getattr(usage, "prompt_tokens_details", None)
    reported_cached = (
        (getattr(prompt_tokens_details, "cached_tokens", 0) or 0)
        if prompt_tokens_details
        else 0
    )
    prompt_cache_hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    reported_cached = reported_cached or prompt_cache_hit

    cache_write_tokens = (
        (getattr(prompt_tokens_details, "cache_write_tokens", 0) or 0)
        if prompt_tokens_details
        else 0
    )

    # 兼容 OpenRouter：cached_tokens 可能包含了 cache_write，需要减去
    cache_read_tokens = (
        max(0, reported_cached - cache_write_tokens)
        if cache_write_tokens > 0
        else reported_cached
    )

    input_tokens = max(0, prompt_tokens - cache_read_tokens - cache_write_tokens)
    # OpenAI 的 completion_tokens 已经包含了 reasoning_tokens
    output_tokens = completion_tokens

    output.usage.input = input_tokens
    output.usage.output = output_tokens
    output.usage.cache_read = cache_read_tokens
    output.usage.cache_write = cache_write_tokens
    output.usage.total_tokens = (
        input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
    )

    calculate_cost(model, output.usage)


def stream_openai_completions(
    model: Model, context: Context, options: Optional[OpenAICompletionsOptions] = None
) -> AssistantMessageEventStream:
    """OpenAI Completions 流式处理主函数"""
    stream = AssistantMessageEventStream()

    async def process_stream():
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
            stop_reason=StopReason.STOP,
            timestamp=int(time.time() * 1000),
        )

        try:
            api_key = options.api_key if options else None
            compat = get_compat(model)
            cache_retention = _resolve_cache_retention(options)
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
                max_retries=options.max_retries if options else None,
            )
            params = build_params(model, context, options, compat, cache_retention)

            if options and options.on_payload:
                payload_result = options.on_payload(params)
                if inspect.isawaitable(payload_result):
                    payload_result = await payload_result
                params = payload_result or params

            timeout = options.timeout if options else None
            request_timeout = timeout if timeout is not None else openai.NOT_GIVEN

            signal = options.signal if options else None
            if signal and getattr(signal, "aborted", False):
                raise Exception("Request was aborted")

            if options and options.on_response:
                raw_response = await client.chat.completions.with_raw_response.create(
                    **params, timeout=request_timeout
                )
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
            else:
                openai_stream = await client.chat.completions.create(
                    **params, timeout=request_timeout
                )

            stream.push(StartEvent(partial=deepcopy(output)))

            current_block = None
            current_block_index = -1
            tool_call_blocks_by_index: Dict[int, ToolCall] = {}
            tool_call_blocks_by_id: Dict[str, ToolCall] = {}
            blocks = output.content

            def get_content_index(block) -> int:
                try:
                    return blocks.index(block)
                except ValueError:
                    return -1

            def finish_block(block):
                if block is None:
                    return
                content_index = get_content_index(block)
                if content_index == -1:
                    return
                if block.type == "text":
                    stream.push(
                        TextEndEvent(
                            content_index=content_index,
                            content=block.text,
                            partial=deepcopy(output),
                        )
                    )
                elif block.type == "thinking":
                    stream.push(
                        ThinkingEndEvent(
                            content_index=content_index,
                            content=block.thinking,
                            partial=deepcopy(output),
                        )
                    )
                elif block.type == "toolCall":
                    parsed = parse_streaming_json(block.partial_args)
                    if isinstance(parsed, dict):
                        block.arguments = parsed
                    # 清理流式解析时的临时字段，避免持久化
                    block.partial_args = None
                    block.stream_index = None
                    stream.push(
                        ToolCallEndEvent(
                            content_index=content_index,
                            tool_call=block,
                            partial=deepcopy(output),
                        )
                    )

            def ensure_text_block():
                nonlocal current_block, current_block_index
                if not current_block or current_block.type != "text":
                    current_block = TextContent(type="text", text="")
                    blocks.append(current_block)
                    current_block_index = len(blocks) - 1
                    stream.push(
                        TextStartEvent(
                            content_index=current_block_index, partial=deepcopy(output)
                        )
                    )
                return current_block

            def ensure_thinking_block(thinking_signature: str):
                nonlocal current_block, current_block_index
                if not current_block or current_block.type != "thinking":
                    current_block = ThinkingContent(
                        type="thinking",
                        thinking="",
                        thinking_signature=thinking_signature,
                    )
                    blocks.append(current_block)
                    current_block_index = len(blocks) - 1
                    stream.push(
                        ThinkingStartEvent(
                            content_index=current_block_index, partial=deepcopy(output)
                        )
                    )
                return current_block

            def ensure_tool_call_block(tool_call_delta):
                nonlocal current_block, current_block_index
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
                    current_block = block
                    current_block_index = len(blocks) - 1
                    stream.push(
                        ToolCallStartEvent(
                            content_index=current_block_index, partial=deepcopy(output)
                        )
                    )
                else:
                    if current_block != block:
                        current_block = block
                        current_block_index = get_content_index(block)

                if isinstance(stream_index, int) and block.stream_index is None:
                    block.stream_index = stream_index
                    tool_call_blocks_by_index[stream_index] = block
                if tool_call_id:
                    tool_call_blocks_by_id[tool_call_id] = block

                return block

            async for chunk in openai_stream:
                if signal and getattr(signal, "aborted", False):
                    await openai_stream.close()
                    break

                if (
                    not chunk
                    or not isinstance(chunk, dict)
                    and not hasattr(chunk, "choices")
                ):
                    continue

                # OpenAI 文档规定 ChatCompletionChunk.id 是每个完成的唯一标识符
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
                    _apply_chunk_usage(output, chunk.usage, model)

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]

                # Fallback: 某些提供商（如 Moonshot）在 choice.usage 中返回 usage
                if not chunk.usage and hasattr(choice, "usage") and choice.usage:
                    _apply_chunk_usage(output, choice.usage, model)

                if choice.finish_reason:
                    stop_reason, error_message = map_stop_reason(choice.finish_reason)
                    output.stop_reason = stop_reason
                    if error_message:
                        output.error_message = error_message

                if choice.delta:
                    delta = choice.delta

                    if delta.content and len(delta.content) > 0:
                        block = ensure_text_block()
                        block.text += delta.content
                        stream.push(
                            TextDeltaEvent(
                                content_index=current_block_index,
                                delta=delta.content,
                                partial=deepcopy(output),
                            )
                        )

                    delta_dict = (
                        delta.model_dump() if hasattr(delta, "model_dump") else {}
                    )
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
                        block = ensure_thinking_block(found_reasoning)
                        delta_text = delta_dict[found_reasoning]
                        block.thinking += delta_text
                        stream.push(
                            ThinkingDeltaEvent(
                                content_index=current_block_index,
                                delta=delta_text,
                                partial=deepcopy(output),
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
                                parsed = parse_streaming_json(block.partial_args)
                                if isinstance(parsed, dict):
                                    block.arguments = parsed

                            content_index = get_content_index(block)
                            if content_index != -1:
                                stream.push(
                                    ToolCallDeltaEvent(
                                        content_index=content_index,
                                        delta=delta_args,
                                        partial=deepcopy(output),
                                    )
                                )

                    if (
                        "reasoning_details" in delta_dict
                        and delta_dict["reasoning_details"]
                    ):
                        reasoning_details = delta_dict["reasoning_details"]
                        if isinstance(reasoning_details, list):
                            for detail in reasoning_details:
                                if (
                                    isinstance(detail, dict)
                                    and detail.get("type") == "reasoning.encrypted"
                                    and detail.get("id")
                                    and detail.get("data")
                                ):
                                    for block in output.content:
                                        if (
                                            block.type == "toolCall"
                                            and block.id == detail.id
                                        ):
                                            block.thought_signature = json.dumps(detail)
                                            break

            for block in blocks:
                finish_block(block)

            if (
                options
                and options.signal
                and hasattr(options.signal, "aborted")
                and options.signal.aborted
            ):
                raise Exception("Request was aborted")

            if output.stop_reason == StopReason.ABORTED:
                raise Exception("Request was aborted")
            if output.stop_reason == StopReason.ERROR:
                raise Exception(
                    output.error_message or "Provider returned an error stop reason"
                )

            stream.push(DoneEvent(reason=output.stop_reason, message=deepcopy(output)))
            stream.end()

        except Exception as e:
            for block in output.content:
                if hasattr(block, "partial_args"):
                    block.partial_args = None
                if hasattr(block, "stream_index"):
                    block.stream_index = None

            output.stop_reason = (
                StopReason.ABORTED
                if (options and options.signal and options.signal.aborted)
                else StopReason.ERROR
            )
            output.error_message = str(e)

            if (
                hasattr(e, "error")
                and hasattr(e.error, "metadata")
                and hasattr(e.error.metadata, "raw")
            ):
                output.error_message += f"\n{e.error.metadata.raw}"

            stream.push(ErrorEvent(reason=output.stop_reason, error=deepcopy(output)))
            stream.end()

    asyncio.create_task(process_stream())
    return stream


def stream_simple_openai_completions(
    model: Model, context: Context, options: Optional[SimpleStreamOptions] = None
) -> AssistantMessageEventStream:
    """简化的OpenAI Completions流式处理"""
    api_key = options.api_key if options else None
    if not api_key:
        api_key = get_env_api_key(model.provider)
    if api_key is None:
        raise RuntimeError(
            f"No API key configured for provider {getattr(model, 'provider', 'unknown')}. "
            "Please set the API key via environment variable or configuration."
        )
    base = build_base_options(model, options, api_key)

    reasoning_effort = None
    if supports_xhigh_thinking(model) and options and options.reasoning:
        reasoning_effort = options.reasoning.value
    elif options and options.reasoning:
        clamped = clamp_reasoning(options.reasoning)
        reasoning_effort = clamped.value if clamped else None

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
        on_payload=base.on_payload,
        on_response=base.on_response,
        metadata=base.metadata,
        timeout=base.timeout,
        max_retries=base.max_retries,
        tool_choice=tool_choice,
        reasoning_effort=reasoning_effort,
    )
    return stream_openai_completions(model, context, openai_options)


class OpenAICompletionsAdapter:
    """
    OpenAI Completions API 适配器

    实现 ApiAdapter Protocol，提供 stream 和 stream_simple 方法。
    """

    api = KnownApi.OPENAI_COMPLETIONS

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[OpenAICompletionsOptions] = None,
    ) -> AssistantMessageEventStream:
        """流式调用"""
        return stream_openai_completions(model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """简化的流式调用"""
        return stream_simple_openai_completions(model, context, options)
