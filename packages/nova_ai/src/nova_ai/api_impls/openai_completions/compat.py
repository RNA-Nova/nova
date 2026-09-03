"""兼容性检测（对齐 TS ``detectCompat`` / ``getCompat``，2026-08 终态）。

自动检测基于 provider 名与 baseUrl 的已知厂商集合；未知 host 走保守默认，
``model.compat`` 显式配置逐字段覆盖检测结果。nova 特有：volcengine /
volces.com / googleapis.com 判定（pi 无此 provider）。
"""

from typing import Optional

from ...types.compat import OpenAICompletionsCompat
from ...types.model import Model


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
    is_moonshot = (
        provider == "moonshotai"
        or provider == "moonshotai-cn"
        or "api.moonshot." in base_url
    )
    is_openrouter = provider == "openrouter" or "openrouter.ai" in base_url
    is_cloudflare_workers_ai = (
        provider == "cloudflare-workers-ai" or "api.cloudflare.com" in base_url
    )
    is_cloudflare_ai_gateway = (
        provider == "cloudflare-ai-gateway" or "gateway.ai.cloudflare.com" in base_url
    )
    is_nvidia = provider == "nvidia" or "integrate.api.nvidia.com" in base_url
    is_ant_ling = provider == "ant-ling" or "api.ant-ling.com" in base_url
    # TS 侧对小写化后匹配 deepseek.com；nova 保持一致
    is_deepseek = (
        provider == "deepseek"
        or "deepseek.com" in base_url.lower()
        or (provider == "volcengine" and model.id.startswith("deepseek"))
    )

    is_non_standard = (
        is_nvidia
        or provider == "cerebras"
        or "cerebras.ai" in base_url
        or provider == "xai"
        or "api.x.ai" in base_url
        or is_together
        or "chutes.ai" in base_url
        or is_deepseek
        or is_zai
        or is_moonshot
        or provider == "opencode"
        or "opencode.ai" in base_url
        or is_cloudflare_workers_ai
        or is_cloudflare_ai_gateway
        or is_ant_ling
        # nova 特有：volcengine / gemini 兼容端点按非标准处理
        or provider == "volcengine"
        or "volces.com" in base_url
        or "googleapis.com" in base_url
    )

    use_max_tokens = (
        "chutes.ai" in base_url
        or is_deepseek
        or is_moonshot
        or is_cloudflare_ai_gateway
        or is_together
        or is_nvidia
        or is_ant_ling
        or is_zai
    )

    is_grok = provider == "xai" or "api.x.ai" in base_url
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
            or is_nvidia
            or is_ant_ling
        ),
        supports_usage_in_streaming=True,
        supports_finish_reason=True,
        max_tokens_field="max_tokens" if use_max_tokens else "max_completion_tokens",
        requires_tool_result_name=False,
        requires_assistant_after_tool_result=False,
        requires_thinking_as_text=False,
        requires_reasoning_content_on_assistant_messages=is_deepseek,
        thinking_format=thinking_format,
        open_router_routing={},
        vercel_gateway_routing={},
        chat_template_kwargs={},
        chat_template_args={},
        zai_tool_stream=False,
        supports_thinking_token_budget=False,
        thinking_token_budget_field=None,
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
    """获取模型的解析后兼容性设置（显式 ``model.compat`` 逐字段覆盖检测值）。"""
    detected = detect_compat(model)
    if model.compat is None:
        return detected
    if not isinstance(model.compat, OpenAICompletionsCompat):
        return detected

    compat = model.compat

    def _pick(explicit, auto):
        return explicit if explicit is not None else auto

    return OpenAICompletionsCompat(
        supports_store=_pick(compat.supports_store, detected.supports_store),
        supports_developer_role=_pick(
            compat.supports_developer_role, detected.supports_developer_role
        ),
        supports_reasoning_effort=_pick(
            compat.supports_reasoning_effort, detected.supports_reasoning_effort
        ),
        supports_usage_in_streaming=_pick(
            compat.supports_usage_in_streaming, detected.supports_usage_in_streaming
        ),
        supports_finish_reason=_pick(
            compat.supports_finish_reason, detected.supports_finish_reason
        ),
        max_tokens_field=_pick(compat.max_tokens_field, detected.max_tokens_field),
        requires_tool_result_name=_pick(
            compat.requires_tool_result_name, detected.requires_tool_result_name
        ),
        requires_assistant_after_tool_result=_pick(
            compat.requires_assistant_after_tool_result,
            detected.requires_assistant_after_tool_result,
        ),
        requires_thinking_as_text=_pick(
            compat.requires_thinking_as_text, detected.requires_thinking_as_text
        ),
        requires_reasoning_content_on_assistant_messages=_pick(
            compat.requires_reasoning_content_on_assistant_messages,
            detected.requires_reasoning_content_on_assistant_messages,
        ),
        thinking_format=_pick(compat.thinking_format, detected.thinking_format),
        open_router_routing=compat.open_router_routing or {},
        vercel_gateway_routing=_pick(
            compat.vercel_gateway_routing, detected.vercel_gateway_routing
        ),
        chat_template_kwargs=_pick(
            compat.chat_template_kwargs, detected.chat_template_kwargs
        ),
        chat_template_args=_pick(
            compat.chat_template_args, detected.chat_template_args
        ),
        zai_tool_stream=_pick(compat.zai_tool_stream, detected.zai_tool_stream),
        supports_thinking_token_budget=_pick(
            compat.supports_thinking_token_budget,
            detected.supports_thinking_token_budget,
        ),
        thinking_token_budget_field=_pick(
            compat.thinking_token_budget_field, detected.thinking_token_budget_field
        ),
        supports_strict_mode=_pick(
            compat.supports_strict_mode, detected.supports_strict_mode
        ),
        cache_control_format=_pick(
            compat.cache_control_format, detected.cache_control_format
        ),
        send_session_affinity_headers=_pick(
            compat.send_session_affinity_headers,
            detected.send_session_affinity_headers,
        ),
        session_affinity_format=_pick(
            compat.session_affinity_format, detected.session_affinity_format
        ),
        supports_long_cache_retention=_pick(
            compat.supports_long_cache_retention,
            detected.supports_long_cache_retention,
        ),
        deferred_tools_mode=_pick(
            compat.deferred_tools_mode, detected.deferred_tools_mode
        ),
    )
