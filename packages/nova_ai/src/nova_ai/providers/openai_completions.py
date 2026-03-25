"""
OpenAI Completions API 流式处理实现
"""

import json
import time
from typing import List, Dict, Optional, Any, Union
import asyncio
from copy import deepcopy
from dataclasses import dataclass

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionAssistantMessageParam,
    ChatCompletionToolMessageParam,
)

from ..core.enums import StopReason
from ..core.messages import (
    Message, UserMessage, AssistantMessage, ToolResultMessage,
    Context, Tool
)
from ..core.content import (
    TextContent, ThinkingContent, ToolCall,
)
from ..models import Model
from ..core.usage import Usage, Cost
from ..utils.env import get_env_api_key
from ..utils.json_parser import parse_streaming_json
from ..utils.surrogate import sanitize_surrogates
from ..utils.copilot import (
    has_copilot_vision_input,
    build_copilot_dynamic_headers
)
from ..utils.stream_options import (
    build_base_options,
    clamp_reasoning,
    StreamOptions,
    SimpleStreamOptions,
)
from ..utils.message_transformer import transform_messages
from ..models import calculate_cost, supports_xhigh_thinking
from ..streaming import (
    AssistantMessageEventStream,
    StartEvent, TextStartEvent, TextDeltaEvent, TextEndEvent,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
    DoneEvent, ErrorEvent
)
from ..compat.openai import OpenAICompletionsCompat

@dataclass
class OpenAICompletionsOptions(StreamOptions):
    """OpenAI Completions 特定选项"""
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    reasoning_effort: Optional[str] = None


def normalize_mistral_tool_id(id: str) -> str:
    """
    规范化工具调用ID以适应Mistral
    
    Mistral要求工具ID正好是9个字母数字字符（a-z, A-Z, 0-9）
    """
    # 移除非字母数字字符
    normalized = ''.join(c for c in id if c.isalnum())
    
    # Mistral要求正好9个字符
    if len(normalized) < 9:
        # 基于原始ID使用确定性字符填充以确保匹配
        padding = "ABCDEFGHI"
        normalized = normalized + padding[:9 - len(normalized)]
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


def map_stop_reason(reason: Optional[str]) -> StopReason:
    """映射OpenAI的finish_reason到标准StopReason"""
    if reason is None:
        return StopReason.STOP
    
    if reason == "stop":
        return StopReason.STOP
    elif reason == "length":
        return StopReason.LENGTH
    elif reason in ["function_call", "tool_calls"]:
        return StopReason.TOOL_USE
    elif reason == "content_filter":
        return StopReason.ERROR
    else:
        raise ValueError(f"Unhandled stop reason: {reason}")


def detect_compat(model: Model) -> OpenAICompletionsCompat:
    """
    从提供商和baseUrl检测兼容性设置
    
    提供商优先于基于URL的检测，因为它是显式配置的
    """
    provider = model.provider
    base_url = model.base_url
    
    is_zai = provider == "zai" or "api.z.ai" in base_url
    
    is_non_standard = (
        provider == "volcengine" or
        "volces.com" in base_url or
        "googleapis.com" in base_url or
        provider == "cerebras" or
        "cerebras.ai" in base_url or
        provider == "xai" or
        "api.x.ai" in base_url or
        provider == "mistral" or
        "mistral.ai" in base_url or
        "chutes.ai" in base_url or
        "deepseek.com" in base_url or
        is_zai or
        provider == "opencode" or
        "opencode.ai" in base_url
    )
    
    use_max_tokens = (
        provider == "mistral" or
        "mistral.ai" in base_url or
        "chutes.ai" in base_url
    )
    
    is_grok = provider == "xai" or "api.x.ai" in base_url
    
    is_mistral = provider == "mistral" or "mistral.ai" in base_url
    
    return OpenAICompletionsCompat(
        supports_store=not is_non_standard,
        supports_developer_role=not is_non_standard,
        supports_reasoning_effort=not (is_grok or is_zai),
        supports_usage_in_streaming=True,
        max_tokens_field="max_tokens" if use_max_tokens else "max_completion_tokens",
        requires_tool_result_name=is_mistral,
        requires_assistant_after_tool_result=False,
        requires_thinking_as_text=is_mistral,
        requires_mistral_tool_ids=is_mistral,
        thinking_format="zai" if is_zai else "openai",
        open_router_routing={},
        vercel_gateway_routing={},
        supports_strict_mode=True,
    )


def get_compat(model: Model) -> OpenAICompletionsCompat:
    """
    获取模型的解析后兼容性设置
    
    如果提供了model.compat则使用，否则自动检测
    """
    detected = detect_compat(model)
    if model.compat is None:
        return detected
    
    compat = model.compat
    
    return OpenAICompletionsCompat(
        supports_store=compat.supports_store if compat.supports_store is not None else detected.supports_store,
        supports_developer_role=compat.supports_developer_role if compat.supports_developer_role is not None else detected.supports_developer_role,
        supports_reasoning_effort=compat.supports_reasoning_effort if compat.supports_reasoning_effort is not None else detected.supports_reasoning_effort,
        supports_usage_in_streaming=compat.supports_usage_in_streaming if compat.supports_usage_in_streaming is not None else detected.supports_usage_in_streaming,
        max_tokens_field=compat.max_tokens_field or detected.max_tokens_field,
        requires_tool_result_name=compat.requires_tool_result_name if compat.requires_tool_result_name is not None else detected.requires_tool_result_name,
        requires_assistant_after_tool_result=compat.requires_assistant_after_tool_result if compat.requires_assistant_after_tool_result is not None else detected.requires_assistant_after_tool_result,
        requires_thinking_as_text=compat.requires_thinking_as_text if compat.requires_thinking_as_text is not None else detected.requires_thinking_as_text,
        requires_mistral_tool_ids=compat.requires_mistral_tool_ids if compat.requires_mistral_tool_ids is not None else detected.requires_mistral_tool_ids,
        thinking_format=compat.thinking_format or detected.thinking_format,
        open_router_routing=compat.open_router_routing or detected.open_router_routing,
        vercel_gateway_routing=compat.vercel_gateway_routing or detected.vercel_gateway_routing,
        supports_strict_mode=compat.supports_strict_mode if compat.supports_strict_mode is not None else detected.supports_strict_mode,
    )


def maybe_add_openrouter_anthropic_cache_control(
    model: Model,
    messages: List[ChatCompletionMessageParam]
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
                    "cache_control": {"type": "ephemeral"}
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
    model: Model,
    context: Context,
    compat: OpenAICompletionsCompat
) -> List[ChatCompletionMessageParam]:
    """将标准消息转换为OpenAI格式"""
    params: List[ChatCompletionMessageParam] = []
    
    def normalize_tool_call_id(id: str) -> str:
        """规范化工具调用ID"""
        if compat.requires_mistral_tool_ids:
            return normalize_mistral_tool_id(id)
        
        if "|" in id:
            call_id = id.split("|")[0]
            import re
            return re.sub(r'[^a-zA-Z0-9_-]', '_', call_id)[:40]
        
        if model.provider == "openai":
            return id[:40] if len(id) > 40 else id
        return id
    
    transformed_messages = transform_messages(
        context.messages,
        model,
        lambda id, m, src: normalize_tool_call_id(id)
    )
    
    if context.system_prompt:
        use_developer_role = model.reasoning and compat.supports_developer_role
        role = "developer" if use_developer_role else "system"
        params.append({
            "role": role,
            "content": sanitize_surrogates(context.system_prompt)
        })
    
    last_role: Optional[str] = None
    
    i = 0
    while i < len(transformed_messages):
        msg = transformed_messages[i]
        
        if (compat.requires_assistant_after_tool_result and 
            last_role == "toolResult" and 
            msg.role == "user"):
            params.append({
                "role": "assistant",
                "content": "I have processed the tool results."
            })
        
        if msg.role == "user":
            user_msg = msg
            if isinstance(user_msg.content, str):
                params.append({
                    "role": "user",
                    "content": sanitize_surrogates(user_msg.content)
                })
            else:
                content = []
                for item in user_msg.content:
                    if item.type == "text":
                        content.append({
                            "type": "text",
                            "text": sanitize_surrogates(item.text)
                        })
                    elif item.type == "image":
                        content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{item.mime_type};base64,{item.data}"
                            }
                        })
                if "image" not in model.input_types:
                    content = [c for c in content if c.get("type") != "image_url"]
                
                if content:
                    params.append({
                        "role": "user",
                        "content": content
                    })
        
        elif msg.role == "assistant":
            assistant_msg = msg
            
            assistant_param: ChatCompletionAssistantMessageParam = {
                "role": "assistant",
                "content": "" if compat.requires_assistant_after_tool_result else None
            }
            
            text_blocks = [b for b in assistant_msg.content if b.type == "text"]
            non_empty_text = [b for b in text_blocks if b.text and b.text.strip()]
            
            if non_empty_text:
                if model.provider == "github-copilot":
                    assistant_param["content"] = "".join(
                        sanitize_surrogates(b.text) for b in non_empty_text
                    )
                else:
                    assistant_param["content"] = [
                        {"type": "text", "text": sanitize_surrogates(b.text)}
                        for b in non_empty_text
                    ]
            
            thinking_blocks = [b for b in assistant_msg.content if b.type == "thinking"]
            non_empty_thinking = [b for b in thinking_blocks if b.thinking and b.thinking.strip()]
            
            if non_empty_thinking:
                if compat.requires_thinking_as_text:
                    thinking_text = "\n\n".join(b.thinking for b in non_empty_thinking)
                    if isinstance(assistant_param["content"], list):
                        assistant_param["content"].insert(0, {"type": "text", "text": thinking_text})
                    else:
                        assistant_param["content"] = [{"type": "text", "text": thinking_text}]
                else:
                    signature = non_empty_thinking[0].thinking_signature
                    if signature:
                        assistant_param[signature] = "\n".join(b.thinking for b in non_empty_thinking)
            
            tool_calls = [b for b in assistant_msg.content if b.type == "toolCall"]
            if tool_calls:
                assistant_param["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in tool_calls
                ]
                
                reasoning_details = []
                for tc in tool_calls:
                    if tc.thought_signature:
                        try:
                            reasoning_details.append(json.loads(tc.thought_signature))
                        except:
                            pass
                if reasoning_details:
                    assistant_param["reasoning_details"] = reasoning_details
            
            content = assistant_param.get("content")
            has_content = (
                content is not None and
                ((isinstance(content, str) and len(content) > 0) or
                 (isinstance(content, list) and len(content) > 0))
            )
            if not has_content and "tool_calls" not in assistant_param:
                i += 1
                continue
            
            params.append(assistant_param)
        
        elif msg.role == "toolResult":
            tool_msg = msg
            image_blocks = []
            
            j = i
            while j < len(transformed_messages) and transformed_messages[j].role == "toolResult":
                curr = transformed_messages[j]
                
                text_result = "\n".join(
                    c.text for c in curr.content if c.type == "text"
                )
                has_images = any(c.type == "image" for c in curr.content)
                
                has_text = len(text_result) > 0
                tool_result_param: ChatCompletionToolMessageParam = {
                    "role": "tool",
                    "content": sanitize_surrogates(text_result if has_text else "(see attached image)"),
                    "tool_call_id": curr.tool_call_id
                }
                if compat.requires_tool_result_name and curr.tool_name:
                    tool_result_param.name = curr.tool_name
                
                params.append(tool_result_param)
                
                if has_images and "image" in model.input_types:
                    for block in curr.content:
                        if block.type == "image":
                            image_blocks.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{block.mime_type};base64,{block.data}"
                                }
                            })
                
                j += 1
            
            i = j - 1
            
            if image_blocks:
                if compat.requires_assistant_after_tool_result:
                    params.append({
                        "role": "assistant",
                        "content": "I have processed the tool results."
                    })
                
                params.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Attached image(s) from tool result:"},
                        *image_blocks
                    ]
                })
                last_role = "user"
            else:
                last_role = "toolResult"
            
            i += 1
            continue
        
        last_role = msg.role
        i += 1
    
    return params


def convert_tools(
    tools: List[Tool],
    compat: OpenAICompletionsCompat
) -> List[Dict[str, Any]]:
    """转换工具定义为OpenAI格式"""
    result = []
    for tool in tools:
        tool_dict = {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters
            }
        }
        if compat.supports_strict_mode is not False:
            tool_dict["function"]["strict"] = False
        
        result.append(tool_dict)
    
    return result


def create_client(
    model: Model,
    context: Context,
    api_key: Optional[str] = None,
    options_headers: Optional[Dict[str, str]] = None
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
    
    if options_headers:
        headers.update(options_headers)
    
    return AsyncOpenAI(
        api_key=api_key,
        base_url=model.base_url,
        default_headers=headers
    )


def build_params(
    model: Model,
    context: Context,
    options: Optional[OpenAICompletionsOptions] = None
) -> Dict[str, Any]:
    """构建OpenAI API参数"""
    compat = get_compat(model)
    messages = convert_messages(model, context, compat)
    maybe_add_openrouter_anthropic_cache_control(model, messages)
    
    params: Dict[str, Any] = {
        "model": model.id,
        "messages": messages,
        "stream": True,
    }
    
    if compat.supports_usage_in_streaming is not False:
        params["stream_options"] = {"include_usage": True}
    
    if compat.supports_store:
        params["store"] = False
    
    if options and options.max_tokens:
        if compat.max_tokens_field == "max_tokens":
            params["max_tokens"] = options.max_tokens
        else:
            params["max_completion_tokens"] = options.max_tokens
    
    if options and options.temperature is not None:
        params["temperature"] = options.temperature
    
    if context.tools:
        params["tools"] = convert_tools(context.tools, compat)
    elif has_tool_history(context.messages):
        params["tools"] = []
    
    if options and options.tool_choice:
        params["tool_choice"] = options.tool_choice
    
    if compat.thinking_format in ["zai", "qwen"] and model.reasoning:
        params["enable_thinking"] = bool(options and options.reasoning_effort)
    elif (options and options.reasoning_effort and 
          model.reasoning and compat.supports_reasoning_effort):
        params["reasoning_effort"] = options.reasoning_effort
    
    if "openrouter.ai" in model.base_url and model.compat and model.compat.open_router_routing:
        params["provider"] = model.compat.open_router_routing
    
    if ("ai-gateway.vercel.sh" in model.base_url and 
        model.compat and model.compat.vercel_gateway_routing):
        routing = model.compat.vercel_gateway_routing
        if routing.only or routing.order:
            gateway_options = {}
            if routing.only:
                gateway_options["only"] = routing.only
            if routing.order:
                gateway_options["order"] = routing.order
            params["providerOptions"] = {"gateway": gateway_options}
    
    return params


async def stream_openai_completions(
    model: Model,
    context: Context,
    options: Optional[OpenAICompletionsOptions] = None
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
                cost=Cost()
            ),
            stop_reason=StopReason.STOP,
            timestamp=int(time.time() * 1000)
        )
        
        try:
            api_key = options.api_key if options else None
            client = create_client(model, context, api_key, options.headers if options else None)
            params = build_params(model, context, options)
            
            if options and options.on_payload:
                options.on_payload(params)
            
            openai_stream = await client.chat.completions.create(**params)
            
            stream.push(StartEvent(partial=deepcopy(output)))
            
            current_block = None
            current_block_index = -1
            
            def finish_current_block(block=None):
                nonlocal current_block, current_block_index
                if block:
                    if block.type == "text":
                        stream.push(TextEndEvent(
                            content_index=current_block_index,
                            content=block,
                            partial=deepcopy(output)
                        ))
                    elif block.type == "thinking":
                        stream.push(ThinkingEndEvent(
                            content_index=current_block_index,
                            content=block,
                            partial=deepcopy(output)
                        ))
                    elif block.type == "toolCall":
                        block.arguments = parse_streaming_json(block.partial_args)
                        if hasattr(block, "partial_args"):
                            del block.partial_args
                        stream.push(ToolCallEndEvent(
                            content_index=current_block_index,
                            tool_call=ToolCall(
                                id=block.id,
                                name=block.name,
                                arguments=block.arguments,
                                thought_signature=block.thought_signature if hasattr(block, "thought_signature") else None
                            ),
                            partial=deepcopy(output)
                        ))
                current_block = None
                current_block_index = -1
            
            async for chunk in openai_stream:
                if chunk.usage:
                    cached_tokens = getattr(chunk.usage.prompt_tokens_details, 'cached_tokens', 0) if chunk.usage.prompt_tokens_details else 0
                    reasoning_tokens = getattr(chunk.usage.completion_tokens_details, 'reasoning_tokens', 0) if chunk.usage.completion_tokens_details else 0
                    
                    input_tokens = (chunk.usage.prompt_tokens or 0) - cached_tokens
                    output_tokens = (chunk.usage.completion_tokens or 0) + reasoning_tokens
                    
                    output.usage.input = input_tokens
                    output.usage.output = output_tokens
                    output.usage.cache_read = cached_tokens
                    output.usage.cache_write = 0
                    output.usage.total_tokens = input_tokens + output_tokens + cached_tokens
                    
                    calculate_cost(model, output.usage)
                
                if not chunk.choices:
                    continue
                
                choice = chunk.choices[0]
                
                if choice.finish_reason:
                    output.stop_reason = map_stop_reason(choice.finish_reason)
                
                if choice.delta:
                    delta = choice.delta
                    
                    if delta.content and len(delta.content) > 0:
                        if not current_block or current_block.type != "text":
                            finish_current_block(current_block)
                            current_block = TextContent(
                                type = "text", 
                                text = ""
                                                        
                            )
                            output.content.append(current_block)
                            current_block_index = len(output.content) - 1
                            stream.push(TextStartEvent(
                                content_index=current_block_index,
                                partial=deepcopy(output)
                            ))
                        
                        if current_block.type == "text":
                            current_block.text += delta.content
                            stream.push(TextDeltaEvent(
                                content_index=current_block_index,
                                delta=delta.content,
                                partial=deepcopy(output)
                            ))
                    
                    delta_dict = delta.model_dump() if hasattr(delta, 'model_dump') else {}
                    reasoning_fields = ["reasoning_content", "reasoning", "reasoning_text"]
                    found_reasoning = None
                    
                    for field in reasoning_fields:
                        if field in delta_dict and delta_dict[field] and len(delta_dict[field]) > 0:
                            found_reasoning = field
                            break
                    
                    if found_reasoning:
                        if not current_block or current_block.type != "thinking":
                            finish_current_block(current_block)
                            current_block = ThinkingContent(
                                type = "thinking",
                                thinking = "",
                                thinking_signature = found_reasoning
                            )
                            output.content.append(current_block)
                            current_block_index = len(output.content) - 1
                            stream.push(ThinkingStartEvent(
                                content_index=current_block_index,
                                partial=deepcopy(output)
                            ))
                        
                        if current_block.type == "thinking":
                            delta_text = delta_dict[found_reasoning]
                            current_block.thinking += delta_text
                            stream.push(ThinkingDeltaEvent(
                                content_index=current_block_index,
                                delta=delta_text,
                                partial=deepcopy(output)
                            ))
                    
                    if delta.tool_calls:
                        for tool_call in delta.tool_calls:
                            if (not current_block or 
                                current_block.type != "toolCall" or
                                (tool_call.id and current_block.id != tool_call.id)):
                                finish_current_block(current_block)
                                current_block = ToolCall(
                                    type="toolCall",
                                    id=tool_call.id or "",
                                    name=tool_call.function.name if tool_call.function else "",
                                    arguments={},
                                )
                                current_block.partial_args=""
                                output.content.append(current_block)
                                current_block_index = len(output.content) - 1
                                stream.push(ToolCallStartEvent(
                                    content_index=current_block_index,
                                    partial=deepcopy(output)
                                ))
                            
                            if current_block.type == "toolCall":
                                if tool_call.id:
                                    current_block.id = tool_call.id
                                if tool_call.function and tool_call.function.name:
                                    current_block.name = tool_call.function.name
                                
                                delta_args = ""
                                if tool_call.function and tool_call.function.arguments:
                                    delta_args = tool_call.function.arguments
                                    current_block.partial_args = current_block.partial_args + delta_args
                                    current_block.arguments = parse_streaming_json(current_block.partial_args)
                                
                                stream.push(ToolCallDeltaEvent(
                                    content_index=current_block_index,
                                    delta=delta_args,
                                    partial=deepcopy(output)
                                ))
                    
                    if "reasoning_details" in delta_dict and delta_dict["reasoning_details"]:
                        reasoning_details = delta_dict["reasoning_details"]
                        if isinstance(reasoning_details, list):
                            for detail in reasoning_details:
                                if (isinstance(detail, dict) and 
                                    detail.get("type") == "reasoning.encrypted" and
                                    detail.get("id") and detail.get("data")):
                                    for block in output.content:
                                        if (block.type == "toolCall" and 
                                            block.id == detail.id):
                                            block.thought_signature = json.dumps(detail)
                                            break
            
            finish_current_block(current_block)
            
            if options and options.signal and hasattr(options.signal, 'aborted') and options.signal.aborted:
                raise Exception("Request was aborted")
            
            if output.stop_reason in [StopReason.ABORTED, StopReason.ERROR]:
                raise Exception("An unknown error occurred")
            
            stream.push(DoneEvent(
                reason=output.stop_reason,
                message=deepcopy(output)
            ))
            stream.end()
            
        except Exception as e:
            for block in output.content:
                if "partial_args" in block:
                    del block.partial_args
            
            output.stop_reason = StopReason.ABORTED if (options and options.signal and options.signal.aborted) else StopReason.ERROR
            output.error_message = str(e)
            
            if hasattr(e, 'error') and hasattr(e.error, 'metadata') and hasattr(e.error.metadata, 'raw'):
                output.error_message += f"\n{e.error.metadata.raw}"
            
            stream.push(ErrorEvent(
                reason=output.stop_reason,
                error=deepcopy(output)
            ))
            stream.end()
    
    asyncio.create_task(process_stream())
    return stream


async def stream_simple_openai_completions(
    model: Model,
    context: Context,
    options: Optional[SimpleStreamOptions] = None
) -> AssistantMessageEventStream:
    """简化的OpenAI Completions流式处理"""
    api_key = options.api_key if options else None
    if not api_key:
        api_key = get_env_api_key(model.provider)
    
    if not api_key:
        raise ValueError(f"No API key for provider: {model.provider}")
    
    base = build_base_options(model, options, api_key)
    
    reasoning_effort = None
    if supports_xhigh_thinking(model) and options and options.reasoning:
        reasoning_effort = options.reasoning.value
    elif options and options.reasoning:
        clamped = clamp_reasoning(options.reasoning)
        reasoning_effort = clamped.value if clamped else None
    
    tool_choice = getattr(options, 'tool_choice', None) if options else None
    
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
        max_retry_delay_ms=base.max_retry_delay_ms,
        metadata=base.metadata,
        tool_choice=tool_choice,
        reasoning_effort=reasoning_effort
    )
    
    # 等待协程执行完成
    return await stream_openai_completions(model, context, openai_options)