"""
OpenAI兼容性配置
"""

from typing import Optional, Literal
from dataclasses import dataclass, field

from ..core.enums import ThinkingFormat
from .routing import OpenRouterRouting, VercelGatewayRouting

from mashumaro.mixins.json import DataClassJSONMixin

@dataclass
class OpenAICompletionsCompat(DataClassJSONMixin):
    """
    OpenAI-compatible completions API 兼容性设置
    用于覆盖基于URL的自动检测
    """
    # 是否支持 `store` 字段。默认：基于URL自动检测
    supports_store: Optional[bool] = None
    
    # 是否支持 `developer` 角色（vs `system`）。默认：基于URL自动检测
    supports_developer_role: Optional[bool] = None
    
    # 是否支持 `reasoning_effort`。默认：基于URL自动检测
    supports_reasoning_effort: Optional[bool] = None
    
    # 是否支持 `stream_options: { include_usage: true }` 用于流式响应中的token使用统计。默认：true
    supports_usage_in_streaming: Optional[bool] = None
    
    # 用于max tokens的字段名。默认：基于URL自动检测
    max_tokens_field: Optional[Literal["max_completion_tokens", "max_tokens"]] = None
    
    # 工具结果是否需要 `name` 字段。默认：基于URL自动检测
    requires_tool_result_name: Optional[bool] = None
    
    # 工具结果后的用户消息是否需要中间的助手消息。默认：基于URL自动检测
    requires_assistant_after_tool_result: Optional[bool] = None
    
    # 思考块是否需要转换为带<thinking>分隔符的文本块。默认：基于URL自动检测
    requires_thinking_as_text: Optional[bool] = None
    
    # 工具调用ID是否需要规范化为Mistral格式（正好9个字母数字字符）。默认：基于URL自动检测
    requires_mistral_tool_ids: Optional[bool] = None
    
    # 推理/思考参数的格式。默认："openai"
    thinking_format: ThinkingFormat = ThinkingFormat.OPENAI
    
    # 是否支持工具定义中的 `strict` 字段。默认：true
    supports_strict_mode: Optional[bool] = None
    
    # 新增字段：OpenRouter-specific routing preferences. Only used when baseUrl points to OpenRouter.
    open_router_routing: Optional[OpenRouterRouting] = None
    
    # 新增字段：Vercel AI Gateway routing preferences. Only used when baseUrl points to Vercel AI Gateway.
    vercel_gateway_routing: Optional[VercelGatewayRouting] = None


@dataclass
class OpenAIResponsesCompat:
    """
    OpenAI Responses API 兼容性设置
    预留供将来使用
    """
    pass