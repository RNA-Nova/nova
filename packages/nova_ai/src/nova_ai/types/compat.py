"""
兼容性配置类型
提供商特定的 API 兼容性设置
"""

from typing import Optional, List, Literal
from pydantic import ConfigDict
from .base_model import NovaBaseModel
from .enums import ThinkingFormat


class OpenRouterRouting(NovaBaseModel):
    """
    OpenRouter 提供商路由偏好设置
    控制OpenRouter将请求路由到哪些上游提供商

    @see https://openrouter.ai/docs/provider-routing
    """

    model_config = ConfigDict(extra="allow")

    # 专门使用的提供商列表（例如 ["amazon-bedrock", "anthropic"]）
    only: Optional[List[str]] = None

    # 按顺序尝试的提供商列表（例如 ["anthropic", "openai"]）
    order: Optional[List[str]] = None


class VercelGatewayRouting(NovaBaseModel):
    """
    Vercel AI Gateway 路由偏好设置
    控制网关将请求路由到哪些上游提供商

    @see https://vercel.com/docs/ai-gateway/models-and-providers/provider-options
    """

    # 专门使用的提供商列表（例如 ["bedrock", "anthropic"]）
    only: Optional[List[str]] = None

    # 按顺序尝试的提供商列表（例如 ["anthropic", "openai"]）
    order: Optional[List[str]] = None


class OpenAICompletionsCompat(NovaBaseModel):
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

    # 推理/思考参数的格式。默认：None（由自动检测决定）
    thinking_format: Optional[ThinkingFormat] = None

    # 是否支持工具定义中的 `strict` 字段。默认：true
    supports_strict_mode: Optional[bool] = None

    # DeepSeek 是否要求在 assistant 消息上提供 reasoning_content 字段
    requires_reasoning_content_on_assistant_messages: Optional[bool] = None

    # 是否发送会话亲和性头部（session_id, x-client-request-id, x-session-affinity）
    send_session_affinity_headers: Optional[bool] = None

    # 是否支持长缓存保留（long cache retention）
    supports_long_cache_retention: Optional[bool] = None

    # Z.ai 提供商是否使用 tool_stream 参数
    zai_tool_stream: Optional[bool] = None

    # 缓存控制格式（例如 "anthropic"）。默认：None
    cache_control_format: Optional[str] = None

    # OpenRouter-specific routing preferences
    open_router_routing: Optional[OpenRouterRouting] = None

    # Vercel AI Gateway routing preferences
    vercel_gateway_routing: Optional[VercelGatewayRouting] = None


class OpenAIResponsesCompat(NovaBaseModel):
    """
    OpenAI Responses API 兼容性设置
    """

    # 是否在启用缓存时发送 OpenAI session_id 缓存亲和性头部
    send_session_id_header: Optional[bool] = None

    # 是否支持长缓存保留（prompt_cache_retention: "24h"）
    supports_long_cache_retention: Optional[bool] = None
