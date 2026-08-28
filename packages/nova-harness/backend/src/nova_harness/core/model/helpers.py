# model_runtime/helpers.py
"""
工具层 - 通用辅助函数与深度合并逻辑
"""

from typing import Dict, Optional

from nova_ai import (
    Model,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    OpenRouterRouting,
    VercelGatewayRouting,
)

from nova_harness.core.types.model import (
    ModelOverride,
    OpenAICompat,
)


def merge_compat(
    base_compat: Optional[OpenAICompat], override_compat: Optional[OpenAICompat]
) -> Optional[OpenAICompat]:
    """
    Deep merge compat settings.
    处理嵌套的 open_router_routing 和 vercel_gateway_routing 合并。
    """
    if override_compat is None:
        return base_compat

    if base_compat is None:
        return override_compat

    # 处理 OpenAICompletionsCompat（具有嵌套路由配置）
    if isinstance(base_compat, OpenAICompletionsCompat) and isinstance(
        override_compat, OpenAICompletionsCompat
    ):
        updates: Dict[str, object] = {}

        # 合并顶层字段
        if override_compat.supports_store is not None:
            updates["supports_store"] = override_compat.supports_store
        if override_compat.supports_developer_role is not None:
            updates["supports_developer_role"] = override_compat.supports_developer_role
        if override_compat.supports_reasoning_effort is not None:
            updates["supports_reasoning_effort"] = (
                override_compat.supports_reasoning_effort
            )
        if override_compat.supports_usage_in_streaming is not None:
            updates["supports_usage_in_streaming"] = (
                override_compat.supports_usage_in_streaming
            )
        if override_compat.max_tokens_field is not None:
            updates["max_tokens_field"] = override_compat.max_tokens_field
        if override_compat.requires_tool_result_name is not None:
            updates["requires_tool_result_name"] = (
                override_compat.requires_tool_result_name
            )
        if override_compat.requires_assistant_after_tool_result is not None:
            updates["requires_assistant_after_tool_result"] = (
                override_compat.requires_assistant_after_tool_result
            )
        if override_compat.requires_thinking_as_text is not None:
            updates["requires_thinking_as_text"] = (
                override_compat.requires_thinking_as_text
            )
        if override_compat.thinking_format is not None:
            updates["thinking_format"] = override_compat.thinking_format

        # 深合并嵌套对象（对齐 TS mergeCompat 的 chatTemplateKwargs 分支）
        if base_compat.chat_template_kwargs or override_compat.chat_template_kwargs:
            updates["chat_template_kwargs"] = {
                **(base_compat.chat_template_kwargs or {}),
                **(override_compat.chat_template_kwargs or {}),
            }

        # 深合并嵌套路由
        if base_compat.open_router_routing or override_compat.open_router_routing:
            base_router = base_compat.open_router_routing
            override_router = override_compat.open_router_routing

            if base_router is not None and override_router is not None:
                updates["open_router_routing"] = OpenRouterRouting(
                    only=(
                        override_router.only
                        if override_router.only is not None
                        else base_router.only
                    ),
                    order=(
                        override_router.order
                        if override_router.order is not None
                        else base_router.order
                    ),
                )
            else:
                updates["open_router_routing"] = override_router or base_router

        if base_compat.vercel_gateway_routing or override_compat.vercel_gateway_routing:
            base_vercel = base_compat.vercel_gateway_routing
            override_vercel = override_compat.vercel_gateway_routing

            if base_vercel is not None and override_vercel is not None:
                updates["vercel_gateway_routing"] = VercelGatewayRouting(
                    only=(
                        override_vercel.only
                        if override_vercel.only is not None
                        else base_vercel.only
                    ),
                    order=(
                        override_vercel.order
                        if override_vercel.order is not None
                        else base_vercel.order
                    ),
                )
            else:
                updates["vercel_gateway_routing"] = override_vercel or base_vercel

        return base_compat.model_copy(update=updates)

    # OpenAIResponsesCompat 当前无嵌套字段，直接覆盖
    if isinstance(override_compat, OpenAIResponsesCompat):
        return override_compat

    # 如果类型不匹配（理论上不应发生），返回 override
    return override_compat


def apply_model_override(model: Model, override: ModelOverride) -> Model:
    """
    Deep merge a model override into a model.
    Handles nested objects (cost, compat) by merging rather than replacing.
    """
    updates: Dict[str, object] = {}

    # 简单字段覆盖
    if override.name is not None:
        updates["name"] = override.name
    if override.reasoning is not None:
        updates["reasoning"] = override.reasoning
    if override.input is not None:
        updates["input_types"] = tuple(override.input)
    if override.context_window is not None:
        updates["context_window"] = override.context_window
    if override.max_tokens is not None:
        updates["max_tokens"] = override.max_tokens

    # 合并 cost（部分覆盖）
    if override.cost is not None and model.cost is not None:
        cost_updates: Dict[str, object] = {}
        if override.cost.input is not None:
            cost_updates["input"] = override.cost.input
        if override.cost.output is not None:
            cost_updates["output"] = override.cost.output
        if override.cost.cache_read is not None:
            cost_updates["cache_read"] = override.cost.cache_read
        if override.cost.cache_write is not None:
            cost_updates["cache_write"] = override.cost.cache_write
        if override.cost.tiers is not None:
            cost_updates["tiers"] = override.cost.tiers
        updates["cost"] = model.cost.model_copy(update=cost_updates)

    # headers 不在此合并：override.headers 的原始模板留在配置里，
    # 请求时由 ModelRuntime 的 model_headers_resolver 解析注入
    # （对齐 TS applyModelOverride 不处理 headers）

    # 深度合并 compat（使用本模块的 merge_compat）
    if override.compat is not None:
        updates["compat"] = merge_compat(model.compat, override.compat)

    # thinking_level_map 覆盖（对齐 TS：按 key 合并而非整体替换）
    if override.thinking_level_map is not None:
        updates["thinking_level_map"] = {
            **(model.thinking_level_map or {}),
            **override.thinking_level_map,
        }

    if updates:
        return model.model_copy(update=updates)
    return model
