"""
兼容性配置类型
提供商特定的 API 兼容性设置
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import ConfigDict, Field

from .base_model import NovaBaseModel
from .cost import ModelCost
from .enums import ThinkingFormat, ThinkingTokenBudgetField

SessionAffinityFormat = Literal["openai", "openai-nosession", "openrouter"]


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

    # 是否保证流式响应携带 finish_reason（False 时无 finish 也按内容推断收尾）
    supports_finish_reason: Optional[bool] = None

    # 顶层思考 token 预算字段（vLLM / Qwen-SGLang / llama.cpp 等共享 max_tokens
    # 的端点用；与 thinking_token_budget_field 二选一）
    supports_thinking_token_budget: Optional[bool] = None

    # 显式指定思考预算字段名（优先于 supports_thinking_token_budget 的默认值）
    thinking_token_budget_field: Optional[ThinkingTokenBudgetField] = None

    # thinking_format 为 "baseten" 时发送的 chat_template_args（字面量或 $var 引用）
    chat_template_args: Optional[Dict[str, Any]] = None

    # 用于max tokens的字段名。默认：基于URL自动检测
    max_tokens_field: Optional[Literal["max_completion_tokens", "max_tokens"]] = None

    # 工具结果是否需要 `name` 字段。默认：基于URL自动检测
    requires_tool_result_name: Optional[bool] = None

    # 工具结果后的用户消息是否需要中间的助手消息。默认：基于URL自动检测
    requires_assistant_after_tool_result: Optional[bool] = None

    # assistant 消息的 null content 是否必须改写为空串（部分端点拒绝
    # null content——对齐 TS requiresNonNullAssistantContent）。默认 false
    requires_non_null_assistant_content: Optional[bool] = None

    # 思考块是否需要转换为带<thinking>分隔符的文本块。默认：基于URL自动检测
    requires_thinking_as_text: Optional[bool] = None

    # 推理/思考参数的格式。默认：None（由自动检测决定）
    thinking_format: Optional[ThinkingFormat] = None

    # thinking_format 为 "chat-template" 时发送的 chat_template_kwargs。
    # 值可以是字面量，也可以是 {"$var": "thinking.enabled"|"thinking.effort", "omitWhenOff": bool}
    # 的变量引用，由请求构建层（nova_ai params.py）按当前思考级别解析。默认：{}
    chat_template_kwargs: Optional[Dict[str, Any]] = None

    # 是否支持工具定义中的 `strict` 字段。默认：true
    supports_strict_mode: Optional[bool] = None

    # DeepSeek 是否要求在 assistant 消息上提供 reasoning_content 字段
    requires_reasoning_content_on_assistant_messages: Optional[bool] = None

    # 是否发送会话亲和性头部
    send_session_affinity_headers: Optional[bool] = None

    # 会话亲和性头部格式
    # - openai: session_id + x-client-request-id + x-session-affinity
    # - openai-nosession: x-client-request-id + x-session-affinity
    # - openrouter: x-session-id
    session_affinity_format: Optional[SessionAffinityFormat] = None

    # prompt_cache_key 请求字段的三态控制（对位 pi promptCacheKeyMode，
    # 0f4d5e960——兼容代理显式开/关缓存键）：
    # - auto（缺省）：api.openai.com 直发，或 long 保留 + supports_long_cache_retention；
    # - enabled：缓存激活（retention 非 none）即发——兼容代理场景；
    # - disabled：永不发送。
    prompt_cache_key_mode: Optional[Literal["auto", "enabled", "disabled"]] = None

    # 是否支持长缓存保留（long cache retention）
    supports_long_cache_retention: Optional[bool] = None

    # Z.ai 提供商是否使用 tool_stream 参数
    zai_tool_stream: Optional[bool] = None

    # 工具 schema 需满足 Moonshot-flavored JSON Schema 子集（nova 特有——
    # kimi/moonshot 系端点拒绝 ``type`` 与 ``anyOf`` 同级共存，400
    # invalid_request_error）；引擎层据此做规范化折叠
    requires_moonshot_flavored_schema: Optional[bool] = None

    # 是否接受会话开始后的 system 文本消息（对位 pi
    # supportsMidConvoSystemMessages；pi.dev 数据携带同名 camelCase 键）
    supports_mid_convo_system_messages: Optional[bool] = None

    # 是否接受会话中途 system 消息引入新工具（对位 pi
    # supportsMidConvoToolAdditions——Kimi K3 的 deferred tool loading；
    # 需 supports_mid_convo_system_messages 同时为真）
    supports_mid_convo_tool_additions: Optional[bool] = None

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

    # 是否支持 `developer` 角色（vs `system`）。默认：true
    supports_developer_role: Optional[bool] = None

    # 会话亲和性头部格式
    session_affinity_format: Optional[SessionAffinityFormat] = None

    # 是否支持长缓存保留（prompt_cache_retention: "24h"）
    supports_long_cache_retention: Optional[bool] = None

    # 是否支持客户端工具搜索（deferred tools）
    supports_tool_search: Optional[bool] = None

    # 是否在启用缓存时发送 OpenAI session_id 缓存亲和性头部
    send_session_id_header: Optional[bool] = None

    # 是否接受对话中段的 system/developer 消息（对齐 TS
    # supportsMidConvoSystemMessages）。默认 false
    supports_mid_convo_system_messages: Optional[bool] = None

    # 是否接受对话中途变更思考级别（对齐 TS supportsMidConvoEffort）。
    # 默认 false
    supports_mid_convo_effort: Optional[bool] = None

    # 是否支持 OpenAI grammar 工具（对齐 TS supportsOpenAIGrammarTools）。
    # 默认 false
    supports_openai_grammar_tools: Optional[bool] = None

    # 是否支持消息锚定的 additional_tools 输入项（对齐 TS
    # supportsAdditionalTools）。默认 false
    supports_additional_tools: Optional[bool] = None

    # 是否支持显式 prompt cache 模式（cacheWrite>0 的模型——对齐 TS
    # supportsExplicitPromptCacheMode）。默认 false
    supports_explicit_prompt_cache_mode: Optional[bool] = None

    # 是否支持 max_output_tokens 字段（对齐 TS supportsMaxOutputTokens）。
    # 默认 false
    supports_max_output_tokens: Optional[bool] = None

    # 是否支持 strict 模式。默认 false
    supports_strict_mode: Optional[bool] = None


class AnthropicAllowedFallbackModel(NovaBaseModel):
    """Anthropic 服务端 refusal fallback 的目标模型（对齐 pi
    ``AnthropicAllowedFallbackModelSchema``）——``cost`` 为本地定价元数据，
    fallback 应答按本费率计成本。"""

    provider: str
    model: str
    cost: ModelCost


class AnthropicMessagesCompat(NovaBaseModel):
    """
    Anthropic Messages API 兼容性设置
    """

    # 是否接受 per-tool eager_input_streaming。默认：true
    supports_eager_tool_input_streaming: Optional[bool] = None

    # 是否支持 Anthropic 长缓存保留（cache_control.ttl: "1h"）。默认：true
    supports_long_cache_retention: Optional[bool] = None

    # 是否发送 x-session-affinity 头部。默认：false
    send_session_affinity_headers: Optional[bool] = None

    # 是否支持 tool params 上的 cache_control。默认：true
    supports_cache_control_on_tools: Optional[bool] = None

    # 是否接受 temperature 字段。默认：true
    supports_temperature: Optional[bool] = None

    # 是否强制 adaptive thinking。默认：false
    force_adaptive_thinking: Optional[bool] = None

    # 是否允许空 thinking signature。默认：false
    allow_empty_signature: Optional[bool] = None

    # 是否支持 Anthropic strict 工具 schema。默认：false
    supports_strict_tools: Optional[bool] = None

    # 是否接受对话中段的 system 消息（新代 Claude 支持——对齐 TS
    # supportsMidConvoSystemMessages）。默认 false
    supports_mid_convo_system_messages: Optional[bool] = None

    # 是否接受对话中途的工具增删（对齐 TS supportsMidConvoToolChanges）。
    # 默认 false
    supports_mid_convo_tool_changes: Optional[bool] = None

    # 是否接受对话中途变更思考级别（对齐 TS supportsMidConvoEffort）。
    # 默认 false
    supports_mid_convo_effort: Optional[bool] = None

    # 是否支持 Anthropic message threads（对齐 TS supportsMessageThreads——
    # 服务端保管线程会话，带 session id 的请求只发增量消息续接上一条
    # 回复）。默认：provider=anthropic 且 baseUrl 在 api.anthropic.com 上
    # （会丢未知请求字段的网关会静默丢掉会话）；转发 thread 字段的网关
    # 可显式开
    supports_message_threads: Optional[bool] = None

    # 服务端 refusal fallback 的许可名单（对齐 pi allowedFallbackModels——
    # 带本地定价，fallback 应答按 fallback 费率计成本；缺省/空 = 调用方
    # 必须省略 fallbacks 字段——无许可目标时 Anthropic 拒收该字段）
    allowed_fallback_models: Optional[List["AnthropicAllowedFallbackModel"]] = Field(
        default=None, max_length=3
    )
