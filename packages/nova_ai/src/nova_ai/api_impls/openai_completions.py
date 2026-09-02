"""
OpenAI Completions API 流式处理实现

对齐 TypeScript ``src/api/openai-completions.ts``。
"""

import asyncio
import inspect
import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import openai
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolMessageParam,
)

from ..streaming import AssistantMessageEventStream
from ..types.compat import OpenAICompletionsCompat
from ..types.content import (
    TextContent,
    ThinkingContent,
    ToolCall,
)
from ..types.enums import KnownApi, ModelThinkingLevel, StopReason
from ..types.events import (
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
from ..types.messages import AssistantMessage, Context, Message, Tool
from ..types.model import Cost, Model, Usage
from ..types.stream_options import ProviderResponse, SimpleStreamOptions, StreamOptions
from ..utils import calculate_cost
from ..utils.copilot import build_copilot_dynamic_headers, has_copilot_vision_input
from ..utils.error_body import format_provider_error, normalize_provider_error
from ..utils.json_parser import parse_streaming_json
from ..utils.message_transformer import transform_messages
from ..utils.model_utils import clamp_thinking_level
from ..utils.simple_options import build_base_options
from ..utils.surrogate import sanitize_surrogates

# API 协议标识（对齐 TS Provider.api 自描述）
api = KnownApi.OPENAI_COMPLETIONS


@dataclass
class OpenAICompletionsOptions(StreamOptions):
    """OpenAI Completions 特定选项（对齐 TS OpenAICompletionsOptions）。"""

    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    reasoning_effort: Optional[str] = None
    parallel_tool_calls: Optional[bool] = None


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _has_header(headers: Optional[Dict[str, Optional[str]]], name: str) -> bool:
    """检查 headers 中是否已存在非空指定头部（对齐 TS hasHeader）。"""
    if not headers:
        return False
    expected = name.lower()
    for key, value in headers.items():
        if key.lower() == expected and value is not None and value.strip():
            return True
    return False


def has_tool_history(messages: List[Message]) -> bool:
    """检查对话消息是否包含工具调用或工具结果（对齐 TS hasToolHistory）。"""
    for msg in messages:
        if msg.role == "toolResult":
            return True
        if msg.role == "assistant":
            if any(block.type == "toolCall" for block in msg.content):
                return True
    return False


def get_deferred_tool_names(messages: List[Message]) -> Set[str]:
    """收集 toolResult 消息中新增注册的工具名（对齐 TS getDeferredToolNames）。"""
    names: Set[str] = set()
    for message in messages:
        if message.role == "toolResult":
            for name in getattr(message, "added_tool_names", None) or []:
                names.add(name)
    return names


def get_tools_by_name(tools: Optional[List[Tool]], names: Set[str]) -> List[Tool]:
    """按名称查找工具定义（对齐 TS getToolsByName）。"""
    if not tools:
        return []
    tools_by_name = {tool.name: tool for tool in tools}
    return [tools_by_name[name] for name in names if name in tools_by_name]


def _is_encrypted_reasoning_detail(detail: Any) -> bool:
    """判断是否为加密的 reasoning detail（对齐 TS isEncryptedReasoningDetail）。"""
    if not isinstance(detail, dict):
        return False
    return (
        detail.get("type") == "reasoning.encrypted"
        and isinstance(detail.get("id"), str)
        and len(detail["id"]) > 0
        and isinstance(detail.get("data"), str)
        and len(detail["data"]) > 0
    )


# ---------------------------------------------------------------------------
# 缓存保留策略
# ---------------------------------------------------------------------------


def resolve_cache_retention(
    cache_retention: Optional[str], env: Optional[Dict[str, str]] = None
) -> str:
    """解析缓存保留策略（对齐 TS resolveCacheRetention）。

    环境变量只认 ``NOVA_CACHE_RETENTION``。
    """
    if cache_retention:
        return cache_retention
    env_value = None
    if env:
        env_value = env.get("NOVA_CACHE_RETENTION")
    if env_value is None:
        import os

        env_value = os.environ.get("NOVA_CACHE_RETENTION")
    return "long" if env_value == "long" else "short"


# ---------------------------------------------------------------------------
# prompt cache key
# ---------------------------------------------------------------------------

OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH = 64


def clamp_openai_prompt_cache_key(key: Optional[str]) -> Optional[str]:
    """截断 prompt_cache_key 到最大长度（对齐 TS clampOpenAIPromptCacheKey）。

    按 Unicode code point 截断，避免截断多字节字符。
    """
    if key is None:
        return None
    chars = list(key)
    if len(chars) <= OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH:
        return key
    return "".join(chars[:OPENAI_PROMPT_CACHE_KEY_MAX_LENGTH])


# ---------------------------------------------------------------------------
# Anthropic 风格 cache_control
# ---------------------------------------------------------------------------


def _get_compat_cache_control(
    compat: OpenAICompletionsCompat, cache_retention: str
) -> Optional[Dict[str, Any]]:
    """根据 compat 和 cache retention 构造 cache_control（对齐 TS getCompatCacheControl）。"""
    if compat.cache_control_format != "anthropic" or cache_retention == "none":
        return None
    control: Dict[str, Any] = {"type": "ephemeral"}
    if cache_retention == "long" and compat.supports_long_cache_retention:
        control["ttl"] = "1h"
    return control


def _add_cache_control_to_text_content(
    message: Dict[str, Any], cache_control: Dict[str, Any]
) -> bool:
    """给消息的文本内容添加 cache_control。"""
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return False
        message["content"] = [
            {"type": "text", "text": content, "cache_control": cache_control}
        ]
        return True
    if not isinstance(content, list):
        return False
    for part in reversed(content):
        if isinstance(part, dict) and part.get("type") == "text":
            part["cache_control"] = cache_control
            return True
    return False


def _add_cache_control_to_system_prompt(
    messages: List[ChatCompletionMessageParam], cache_control: Dict[str, Any]
) -> None:
    """给第一条 system/developer 消息加 cache_control。"""
    for msg in messages:
        role = msg.get("role")
        if role in ("system", "developer"):
            _add_cache_control_to_text_content(msg, cache_control)
            return


def _add_cache_control_to_last_conversation_message(
    messages: List[ChatCompletionMessageParam], cache_control: Dict[str, Any]
) -> None:
    """给最后一条 user/assistant 消息加 cache_control。"""
    for msg in reversed(messages):
        role = msg.get("role")
        if role in ("user", "assistant"):
            if _add_cache_control_to_text_content(msg, cache_control):
                return


def _add_cache_control_to_last_tool(
    tools: Optional[List[Dict[str, Any]]], cache_control: Dict[str, Any]
) -> None:
    """给最后一条 tool 定义加 cache_control。"""
    if not tools:
        return
    tools[-1]["cache_control"] = cache_control


def _apply_anthropic_cache_control(
    messages: List[ChatCompletionMessageParam],
    tools: Optional[List[Dict[str, Any]]],
    cache_control: Dict[str, Any],
) -> None:
    """应用 Anthropic 风格缓存标记（system + 最后 tool + 最后对话消息）。"""
    _add_cache_control_to_system_prompt(messages, cache_control)
    _add_cache_control_to_last_tool(tools, cache_control)
    _add_cache_control_to_last_conversation_message(messages, cache_control)


# ---------------------------------------------------------------------------
# 兼容性检测
# ---------------------------------------------------------------------------


def detect_compat(model: Model) -> OpenAICompletionsCompat:
    """从提供商和 baseUrl 检测兼容性设置（对齐 TS detectCompat）。"""
    provider = model.provider
    base_url = model.base_url

    is_zai = (
        provider == "zai"
        or provider == "zai-coding-cn"
        or "api.z.ai" in base_url
        or "open.bigmodel.cn" in base_url
    )
    is_together = (
        provider == "together"
        or "api.together.ai" in base_url
        or "api.together.xyz" in base_url
    )
    is_ant_ling = provider == "ant-ling" or "api.ant-ling.com" in base_url
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
    is_nvidia = provider == "nvidia" or "integrate.api.nvidia.com" in base_url

    is_non_standard = (
        provider == "volcengine"
        or "volces.com" in base_url
        or "googleapis.com" in base_url
        or provider == "cerebras"
        or "cerebras.ai" in base_url
        or provider == "xai"
        or "api.x.ai" in base_url
        or "chutes.ai" in base_url
        or "deepseek.com" in base_url
        or is_zai
        or provider == "opencode"
        or "opencode.ai" in base_url
        or is_together
        or is_moonshot
        or is_cloudflare_workers_ai
        or is_cloudflare_ai_gateway
        or is_ant_ling
        or is_nvidia
    )

    use_max_tokens = (
        "chutes.ai" in base_url
        or is_moonshot
        or is_cloudflare_ai_gateway
        or is_together
        or is_ant_ling
        or is_nvidia
    )

    is_grok = provider == "xai" or "api.x.ai" in base_url
    is_deepseek = (
        provider == "deepseek"
        or "deepseek.com" in base_url
        or (provider == "volcengine" and model.id.startswith("deepseek"))
    )
    is_openrouter = provider == "openrouter" or "openrouter.ai" in base_url
    is_openrouter_developer_role_model = is_openrouter and (
        model.id.startswith("anthropic/") or model.id.startswith("openai/")
    )
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
    elif is_ant_ling:
        thinking_format = "ant-ling"
    elif is_openrouter:
        thinking_format = "openrouter"

    return OpenAICompletionsCompat(
        supports_store=not is_non_standard,
        supports_developer_role=(
            is_openrouter_developer_role_model
            or (not is_non_standard and not is_openrouter)
        ),
        supports_reasoning_effort=not (
            is_grok
            or is_zai
            or is_moonshot
            or is_together
            or is_cloudflare_ai_gateway
            or is_ant_ling
            or is_nvidia
        ),
        supports_usage_in_streaming=True,
        max_tokens_field="max_tokens" if use_max_tokens else "max_completion_tokens",
        requires_tool_result_name=False,
        requires_assistant_after_tool_result=False,
        requires_thinking_as_text=False,
        requires_reasoning_content_on_assistant_messages=is_deepseek,
        thinking_format=thinking_format,
        open_router_routing={},
        vercel_gateway_routing={},
        chat_template_kwargs={},
        zai_tool_stream=False,
        supports_strict_mode=not (
            is_moonshot or is_together or is_cloudflare_ai_gateway or is_nvidia
        ),
        cache_control_format=cache_control_format,
        send_session_affinity_headers=False,
        deferred_tools_mode=None,
        session_affinity_format="openrouter" if is_openrouter else "openai",
        supports_long_cache_retention=not (
            is_together
            or is_cloudflare_workers_ai
            or is_cloudflare_ai_gateway
            or is_nvidia
            or is_ant_ling
        ),
    )


def get_compat(model: Model) -> OpenAICompletionsCompat:
    """获取模型的解析后兼容性设置（对齐 TS getCompat）。"""
    detected = detect_compat(model)
    if model.compat is None:
        return detected
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
        requires_reasoning_content_on_assistant_messages=(
            compat.requires_reasoning_content_on_assistant_messages
            if compat.requires_reasoning_content_on_assistant_messages is not None
            else detected.requires_reasoning_content_on_assistant_messages
        ),
        thinking_format=compat.thinking_format or detected.thinking_format,
        open_router_routing=compat.open_router_routing or detected.open_router_routing,
        vercel_gateway_routing=compat.vercel_gateway_routing
        or detected.vercel_gateway_routing,
        chat_template_kwargs=(
            compat.chat_template_kwargs
            if compat.chat_template_kwargs is not None
            else detected.chat_template_kwargs
        ),
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
        session_affinity_format=(
            compat.session_affinity_format
            if compat.session_affinity_format is not None
            else detected.session_affinity_format
        ),
        supports_long_cache_retention=(
            compat.supports_long_cache_retention
            if compat.supports_long_cache_retention is not None
            else detected.supports_long_cache_retention
        ),
        deferred_tools_mode=(
            compat.deferred_tools_mode
            if compat.deferred_tools_mode is not None
            else detected.deferred_tools_mode
        ),
    )


# ---------------------------------------------------------------------------
# 消息转换
# ---------------------------------------------------------------------------


def convert_messages(
    model: Model, context: Context, compat: OpenAICompletionsCompat
) -> List[ChatCompletionMessageParam]:
    """把内部消息列表转换为 OpenAI Chat Completions 参数（对齐 TS convertMessages）。"""

    def normalize_tool_call_id(id: str) -> str:
        """规范化工具调用 ID（对齐 TS normalizeToolCallId）。"""
        if "|" in id:
            call_id = id.split("|")[0]
            return re.sub(r"[^a-zA-Z0-9_-]", "_", call_id)[:40]
        if model.provider == "openai":
            return id[:40] if len(id) > 40 else id
        return id

    transformed_messages = transform_messages(
        context.messages, model, lambda id, m, src: normalize_tool_call_id(id)
    )

    params: List[ChatCompletionMessageParam] = []

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
                if not content:
                    i += 1
                    continue
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
                    thinking_text = "\n\n".join(
                        sanitize_surrogates(b.thinking) for b in non_empty_thinking
                    )
                    assistant_param["content"] = [
                        {"type": "text", "text": thinking_text},
                        *assistant_text_parts,
                    ]
                else:
                    if assistant_text:
                        assistant_param["content"] = assistant_text
                    signature = non_empty_thinking[0].thinking_signature
                    if model.provider == "opencode-go" and signature == "reasoning":
                        signature = "reasoning_content"
                    if signature and len(signature) > 0:
                        assistant_param[signature] = "\n".join(
                            b.thinking for b in non_empty_thinking
                        )
            elif assistant_text:
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
            deferred_tool_names: Set[str] = set()

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
                if has_text:
                    tool_result_text = text_result
                elif has_images:
                    tool_result_text = "(see attached image)"
                else:
                    tool_result_text = "(no tool output)"
                tool_result_param: ChatCompletionToolMessageParam = {
                    "role": "tool",
                    "content": sanitize_surrogates(tool_result_text),
                    "tool_call_id": curr.tool_call_id,
                }
                if compat.requires_tool_result_name and curr.tool_name:
                    tool_result_param["name"] = curr.tool_name  # type: ignore[typeddict-unknown-key]
                params.append(tool_result_param)

                if compat.deferred_tools_mode == "kimi":
                    for name in getattr(curr, "added_tool_names", None) or []:
                        deferred_tool_names.add(name)

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

            if compat.deferred_tools_mode == "kimi" and deferred_tool_names:
                deferred_tools = get_tools_by_name(context.tools, deferred_tool_names)
                if deferred_tools:
                    params.append(
                        {
                            "role": "system",
                            "tools": convert_tools(deferred_tools, compat),
                        }
                    )

            i += 1
            continue

        last_role = msg.role
        i += 1

    return params


def convert_tools(
    tools: List[Tool], compat: OpenAICompletionsCompat
) -> List[Dict[str, Any]]:
    """转换工具定义为 OpenAI 格式（对齐 TS convertTools）。"""
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


# ---------------------------------------------------------------------------
# 客户端创建
# ---------------------------------------------------------------------------


def create_client(
    model: Model,
    context: Context,
    api_key: Optional[str] = None,
    options_headers: Optional[Dict[str, Optional[str]]] = None,
    session_id: Optional[str] = None,
    compat: Optional[OpenAICompletionsCompat] = None,
    max_retries: Optional[int] = None,
) -> AsyncOpenAI:
    """创建 OpenAI 客户端（对齐 TS createClient）。"""
    resolved_compat = compat or get_compat(model)

    headers: Dict[str, Optional[str]] = {}
    if model.headers:
        headers.update(model.headers)

    if model.provider == "github-copilot":
        has_images = has_copilot_vision_input(context.messages)
        copilot_headers = build_copilot_dynamic_headers(context.messages, has_images)
        headers.update(copilot_headers)

    if session_id and resolved_compat.send_session_affinity_headers:
        fmt = resolved_compat.session_affinity_format or "openai"
        if fmt == "openrouter":
            headers["x-session-id"] = session_id
        else:
            if fmt == "openai":
                headers["session_id"] = session_id
            headers["x-client-request-id"] = session_id
            headers["x-session-affinity"] = session_id

    if options_headers:
        headers.update(options_headers)

    if (
        not api_key
        and not _has_header(headers, "authorization")
        and not _has_header(headers, "cf-aig-authorization")
    ):
        # 对齐 TS getClientApiKey：协议层不读环境变量，api key 必须由上游
        # （Models.applyAuth / 调用方 options）注入；headers 自带 auth 时经
        # client_kwargs 的 "unused" 占位放行。
        raise ValueError(f"No API key for provider: {model.provider}")

    # Cloudflare AI Gateway 特殊鉴权头部
    if model.provider == "cloudflare-ai-gateway":
        default_headers: Dict[str, Optional[str]] = {
            **headers,
            "Authorization": headers.get("Authorization") or "",
            "cf-aig-authorization": f"Bearer {api_key}",
        }
    else:
        default_headers = headers

    client_kwargs: Dict[str, Any] = {
        "api_key": api_key or "unused",
        "base_url": model.base_url,
        "default_headers": {k: v for k, v in default_headers.items() if v is not None},
        "max_retries": max_retries if max_retries is not None else 0,
    }

    return AsyncOpenAI(**client_kwargs)


# ---------------------------------------------------------------------------
# 请求参数构建
# ---------------------------------------------------------------------------


def build_params(
    model: Model,
    context: Context,
    options: Optional[OpenAICompletionsOptions] = None,
    compat: Optional[OpenAICompletionsCompat] = None,
    cache_retention: Optional[str] = None,
) -> Dict[str, Any]:
    """构建 OpenAI API 请求体参数（对齐 TS buildParams）。"""
    compat = compat or get_compat(model)
    cache_retention = cache_retention or resolve_cache_retention(
        options.cache_retention if options else None,
        options.env if options else None,
    )

    messages = convert_messages(model, context, compat)
    cache_control = _get_compat_cache_control(compat, cache_retention)

    reasoning_effort = getattr(options, "reasoning_effort", None) if options else None
    enabled = bool(reasoning_effort and reasoning_effort != "off")

    def _map_reasoning_effort(effort: Optional[str]) -> Optional[str]:
        if effort and model.thinking_level_map and effort in model.thinking_level_map:
            mapped = model.thinking_level_map[effort]
            if mapped is not None:
                return mapped
        return effort

    def _off_mapped_value() -> Optional[str]:
        if model.thinking_level_map:
            off = model.thinking_level_map.get("off")
            if isinstance(off, str):
                return off
        return None

    def _off_is_explicitly_null() -> bool:
        return (
            model.thinking_level_map is not None
            and "off" in model.thinking_level_map
            and model.thinking_level_map["off"] is None
        )

    def _map_reasoning_effort_strict(effort: Optional[str]) -> Optional[str]:
        if not effort or not model.thinking_level_map:
            return effort
        if effort not in model.thinking_level_map:
            return effort
        return model.thinking_level_map[effort]

    _OMIT = object()

    def _resolve_chat_kwarg_value(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if not reasoning_effort and value.get("omitWhenOff"):
            return _OMIT
        if value.get("$var") == "thinking.enabled":
            return bool(reasoning_effort)
        level_map = model.thinking_level_map or {}
        key = reasoning_effort if reasoning_effort else "off"
        if key not in level_map:
            return reasoning_effort if reasoning_effort else _OMIT
        mapped = level_map[key]
        return mapped if isinstance(mapped, str) else _OMIT

    def _build_chat_template_kwargs() -> Optional[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {}
        for key, value in (compat.chat_template_kwargs or {}).items():
            resolved = _resolve_chat_kwarg_value(value)
            if resolved is not _OMIT:
                kwargs[key] = resolved
        return kwargs or None

    final_reasoning_effort = (
        _map_reasoning_effort(reasoning_effort) if enabled else None
    )

    params: Dict[str, Any] = {
        "model": model.id,
        "messages": messages,
        "stream": True,
    }

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

    deferred_tool_names: Set[str] = set()
    if compat.deferred_tools_mode == "kimi":
        deferred_tool_names = get_deferred_tool_names(context.messages)

    active_tools = None
    if context.tools:
        active_tools = [
            tool for tool in context.tools if tool.name not in deferred_tool_names
        ]

    if active_tools:
        params["tools"] = convert_tools(active_tools, compat)
        if compat.zai_tool_stream:
            params["tool_stream"] = True
        if options and getattr(options, "parallel_tool_calls", None) is not None:
            params["parallel_tool_calls"] = options.parallel_tool_calls
    elif has_tool_history(context.messages):
        params["tools"] = []

    if options and getattr(options, "tool_choice", None):
        params["tool_choice"] = options.tool_choice

    if cache_control:
        _apply_anthropic_cache_control(messages, params.get("tools"), cache_control)

    # 非标准字段统一走 extra_body
    extra_body: Dict[str, Any] = {}

    use_prompt_cache_key = (
        options
        and options.session_id
        and (
            ("api.openai.com" in model.base_url and cache_retention != "none")
            or (cache_retention == "long" and compat.supports_long_cache_retention)
        )
    )
    if use_prompt_cache_key:
        extra_body["prompt_cache_key"] = clamp_openai_prompt_cache_key(
            options.session_id
        )

    if cache_retention == "long" and compat.supports_long_cache_retention:
        extra_body["prompt_cache_retention"] = "24h"

    # 推理/思考参数格式处理
    if model.reasoning:
        if compat.thinking_format == "zai":
            if enabled:
                extra_body["thinking"] = {"type": "enabled", "clear_thinking": False}
            else:
                extra_body["thinking"] = {"type": "disabled"}
            if enabled and compat.supports_reasoning_effort:
                effort = _map_reasoning_effort_strict(reasoning_effort)
                if effort is not None:
                    params["reasoning_effort"] = effort
        elif compat.thinking_format == "qwen":
            extra_body["enable_thinking"] = enabled
        elif compat.thinking_format == "qwen-chat-template":
            extra_body["chat_template_kwargs"] = {
                "enable_thinking": enabled,
                "preserve_thinking": True,
            }
        elif compat.thinking_format == "chat-template":
            chat_kwargs = _build_chat_template_kwargs()
            if chat_kwargs:
                extra_body["chat_template_kwargs"] = chat_kwargs
        elif compat.thinking_format == "deepseek":
            if enabled:
                extra_body["thinking"] = {"type": "enabled"}
            elif not _off_is_explicitly_null():
                extra_body["thinking"] = {"type": "disabled"}
            if enabled and compat.supports_reasoning_effort:
                params["reasoning_effort"] = final_reasoning_effort
        elif compat.thinking_format == "openrouter":
            if enabled:
                extra_body["reasoning"] = {"effort": final_reasoning_effort}
            elif not _off_is_explicitly_null():
                extra_body["reasoning"] = {"effort": _off_mapped_value() or "none"}
        elif compat.thinking_format == "ant-ling":
            if enabled and model.thinking_level_map:
                effort = model.thinking_level_map.get(reasoning_effort)
                if isinstance(effort, str):
                    extra_body["reasoning"] = {"effort": effort}
        elif compat.thinking_format == "together":
            extra_body["reasoning"] = {"enabled": enabled}
            if enabled and compat.supports_reasoning_effort:
                params["reasoning_effort"] = final_reasoning_effort
        elif compat.thinking_format == "string-thinking":
            if enabled:
                extra_body["thinking"] = final_reasoning_effort
            elif not _off_is_explicitly_null():
                extra_body["thinking"] = _off_mapped_value() or "none"
        elif enabled and compat.supports_reasoning_effort:
            params["reasoning_effort"] = final_reasoning_effort
        elif compat.supports_reasoning_effort:
            off_value = _off_mapped_value()
            if off_value is not None:
                params["reasoning_effort"] = off_value

    # OpenRouter / Vercel 路由偏好：以模型自身 compat 为准（对齐 TS），
    # 不额外要求 base_url 匹配，自定义网关/代理也可用。
    model_compat = (
        model.compat if isinstance(model.compat, OpenAICompletionsCompat) else None
    )
    if model_compat and model_compat.open_router_routing:
        extra_body["provider"] = model_compat.open_router_routing.model_dump(
            exclude_none=True
        )

    if model_compat and model_compat.vercel_gateway_routing:
        routing = model_compat.vercel_gateway_routing
        if routing.only or routing.order:
            gateway_options: Dict[str, Any] = {}
            if routing.only:
                gateway_options["only"] = routing.only
            if routing.order:
                gateway_options["order"] = routing.order
            extra_body["providerOptions"] = {"gateway": gateway_options}

    params = {k: v for k, v in params.items() if v is not None}
    if extra_body:
        params["extra_body"] = extra_body

    return params


# ---------------------------------------------------------------------------
# usage 解析
# ---------------------------------------------------------------------------


def parse_chunk_usage(raw_usage: Any, model: Model) -> Usage:
    """解析 chunk 中的 usage 信息（对齐 TS parseChunkUsage）。"""
    prompt_tokens = getattr(raw_usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(raw_usage, "completion_tokens", 0) or 0

    prompt_tokens_details = getattr(raw_usage, "prompt_tokens_details", None)
    cache_read_tokens = (
        (getattr(prompt_tokens_details, "cached_tokens", 0) or 0)
        if prompt_tokens_details
        else 0
    )
    prompt_cache_hit = getattr(raw_usage, "prompt_cache_hit_tokens", 0) or 0
    cache_read_tokens = cache_read_tokens or prompt_cache_hit

    cache_write_tokens = (
        (getattr(prompt_tokens_details, "cache_write_tokens", 0) or 0)
        if prompt_tokens_details
        else 0
    )

    input_tokens = max(0, prompt_tokens - cache_read_tokens - cache_write_tokens)
    output_tokens = completion_tokens

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


# ---------------------------------------------------------------------------
# stop reason 映射
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 流式调用
# ---------------------------------------------------------------------------


def stream(
    model: Model, context: Context, options: Optional[OpenAICompletionsOptions] = None
) -> AssistantMessageEventStream:
    """OpenAI Completions 流式处理主函数（对齐 TS stream）。"""
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
            stop_reason=StopReason.STOP,
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
        pending_reasoning_details_by_tool_call_id: Dict[str, str] = {}

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
                        partial=deepcopy(output),
                    )
                )
            elif block.type == "thinking":
                event_stream.push(
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
                block.partial_args = None
                block.stream_index = None
                event_stream.push(
                    ToolCallEndEvent(
                        content_index=content_index,
                        tool_call=block,
                        partial=deepcopy(output),
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
                        partial=deepcopy(output),
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
                        partial=deepcopy(output),
                    )
                )
            return thinking_block

        def _apply_pending_reasoning_detail(block: ToolCall) -> None:
            if not block.id:
                return
            pending = pending_reasoning_details_by_tool_call_id.get(block.id)
            if pending is not None:
                block.thought_signature = pending
                del pending_reasoning_details_by_tool_call_id[block.id]

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
                event_stream.push(
                    ToolCallStartEvent(
                        content_index=get_content_index(block),
                        partial=deepcopy(output),
                    )
                )

            if isinstance(stream_index, int) and block.stream_index is None:
                block.stream_index = stream_index
                tool_call_blocks_by_index[stream_index] = block
            if tool_call_id:
                tool_call_blocks_by_id[tool_call_id] = block

            _apply_pending_reasoning_detail(block)
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
                max_retries=options.max_retries if options else None,
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
                raise Exception("Request was aborted")

            raw_response = await client.chat.completions.with_raw_response.create(
                **params, timeout=request_timeout
            )
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

            event_stream.push(StartEvent(partial=deepcopy(output)))

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

                if not chunk.usage and hasattr(choice, "usage") and choice.usage:
                    output.usage = parse_chunk_usage(choice.usage, model)

                if choice.finish_reason:
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
                                event_stream.push(
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
                                if _is_encrypted_reasoning_detail(detail):
                                    serialized = json.dumps(detail)
                                    block = tool_call_blocks_by_id.get(detail["id"])
                                    if block is not None:
                                        block.thought_signature = serialized
                                    else:
                                        pending_reasoning_details_by_tool_call_id[
                                            detail["id"]
                                        ] = serialized

            finish_all_blocks()

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
            if not has_finish_reason:
                raise Exception("Stream ended without finish_reason")

            event_stream.push(
                DoneEvent(reason=output.stop_reason, message=deepcopy(output))
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
                ErrorEvent(reason=output.stop_reason, error=deepcopy(output))
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
    """简化的 OpenAI Completions 流式处理（对齐 TS streamSimple）。"""
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
        tool_choice=tool_choice,
        reasoning_effort=reasoning_effort,
    )
    return stream(model, context, openai_options)
