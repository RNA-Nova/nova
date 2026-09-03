"""请求体参数构建（对齐 TS ``buildParams``，2026-08 终态）。

extra_body 纪律：openai-python 拒绝未知 kwarg——所有非标准字段
（``prompt_cache_key`` / ``tool_stream`` / ``thinking.*`` /
``chat_template_*`` / 思考预算字段 / ``reasoning_details``）统一经
``extra_body`` 汇入线上 JSON 顶层。禁止散落 ``params["x"] = ...``。
"""

from typing import Any, Dict, Optional

from ...types.compat import OpenAICompletionsCompat
from ...types.messages import Context
from ...types.model import Model
from .._shared.prompt_cache import (
    _apply_anthropic_cache_control,
    _get_compat_cache_control,
    clamp_openai_prompt_cache_key,
    resolve_cache_retention,
)
from .._shared.simple_options import (
    clamp_thinking_budget_to_answer_room,
    thinking_budget_for_level,
)
from .compat import get_compat
from .messages import (
    convert_messages,
    convert_tools,
    get_deferred_tool_names,
    has_tool_history,
)
from .options import OpenAICompletionsOptions

_OMIT = object()


def _resolve_thinking_token_budget_field(
    compat: OpenAICompletionsCompat,
) -> Optional[str]:
    """解析顶层思考预算字段（显式字段名优先；对齐 TS resolveThinkingTokenBudgetField）。"""
    if compat.thinking_token_budget_field:
        return compat.thinking_token_budget_field
    if compat.supports_thinking_token_budget:
        return "thinking_token_budget"
    return None


def _map_level(level_map: Dict[str, Optional[str]], level: str) -> Optional[str]:
    """映射思考级别；键缺失或显式 ``None``（该级别不受支持）→ ``None``。

    两种"不映射"必须区分——键缺失回落原值；显式 ``None``（该级别不受支持）
    返回 ``None``，由调用方省略字段（绝不能把 ``null`` 发上线）。
    """
    if level not in level_map:
        return level
    return level_map[level]


def _resolve_clamped_thinking_budget(
    model: Model,
    options: Optional[OpenAICompletionsOptions],
    params: Dict[str, Any],
) -> Optional[int]:
    """按答案余量钳制思考预算（对齐 TS resolveClampedThinkingBudget）。"""
    # 专属字段经 getattr 防御（TS ?. 的 Python 对位）：Models 高层路径
    # 传的可能是 SimpleStreamOptions 等通用基类，不带协议专属字段。
    effort = getattr(options, "reasoning_effort", None) if options else None
    # "off" 是显式关闭：调用方已归一为不携带 reasoning_effort，
    # 这里也不能让真值字符串 "off" 落进默认预算表（medium=8192）
    if not effort or effort == "off" or not model.reasoning:
        return None
    ceiling = (
        params.get("max_tokens")
        or params.get("max_completion_tokens")
        or model.max_tokens
    )
    budget = clamp_thinking_budget_to_answer_room(
        thinking_budget_for_level(
            effort,
            getattr(options, "thinking_budgets", None),
        ),
        int(ceiling),
    )
    return budget if budget > 0 else None


def _build_chat_template_values(
    model: Model,
    options: Optional[OpenAICompletionsOptions],
    values: Dict[str, Any],
    thinking_budget: Optional[int],
    reasoning_effort: Optional[str],
) -> Optional[Dict[str, Any]]:
    """解析 $var 变量引用的 chat template 值表（对齐 TS buildChatTemplateValues）。"""
    resolved: Dict[str, Any] = {}
    for key, value in values.items():
        result = _resolve_chat_template_kwarg_value(
            model, options, value, thinking_budget, reasoning_effort
        )
        if result is not _OMIT:
            resolved[key] = result
    return resolved or None


def _resolve_chat_template_kwarg_value(
    model: Model,
    options: Optional[OpenAICompletionsOptions],
    value: Any,
    thinking_budget: Optional[int],
    reasoning_effort: Optional[str],
) -> Any:
    if not isinstance(value, dict):
        return value
    if not reasoning_effort and value.get("omitWhenOff"):
        return _OMIT
    var = value.get("$var")
    if var == "thinking.enabled":
        return bool(reasoning_effort)
    if var == "thinking.budget":
        return thinking_budget if thinking_budget is not None else _OMIT
    level_map = model.thinking_level_map or {}
    key = reasoning_effort if reasoning_effort else "off"
    if key not in level_map:
        return reasoning_effort if reasoning_effort else _OMIT
    mapped = level_map[key]
    return mapped if isinstance(mapped, str) else _OMIT


def build_params(
    model: Model,
    context: Context,
    options: Optional[OpenAICompletionsOptions] = None,
    compat: Optional[OpenAICompletionsCompat] = None,
    cache_retention: Optional[str] = None,
) -> Dict[str, Any]:
    """构建 OpenAI API 请求体参数（对齐 TS buildParams 终态）。"""
    compat = compat or get_compat(model)
    cache_retention = cache_retention or resolve_cache_retention(
        options.cache_retention if options else None,
        options.env if options else None,
    )

    messages = convert_messages(model, context, compat)
    cache_control = _get_compat_cache_control(compat, cache_retention)

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

    if options and getattr(options, "metadata", None):
        # 旧单体遗漏修复：OpenAI store:true 场景的轨迹追踪 metadata
        params["metadata"] = options.metadata

    deferred_tool_names = set()
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
        # Anthropic（经 LiteLLM/代理）要求对话含工具调用/结果时必须带 tools 参数
        params["tools"] = []

    if cache_control:
        _apply_anthropic_cache_control(messages, params.get("tools"), cache_control)

    # 双条件（对齐 TS）：tools 非空才发 tool_choice——严格端点对
    # "有 choice 无 tools" 直接 400。
    if options and getattr(options, "tool_choice", None) and params.get("tools"):
        params["tool_choice"] = options.tool_choice

    # ---- 思考参数（按 thinking_format 分派；对齐 TS 分支序）----
    # "off" 入口归一为 None：pi 的 reasoningEffort 类型层不含 off
    # （stream_simple 已过滤），但本层类型是宽松 str——归一保证任何
    # 分支都不会把 "off" 泄漏给请求体。
    reasoning_effort = getattr(options, "reasoning_effort", None) if options else None
    if reasoning_effort == "off":
        reasoning_effort = None
    enabled = bool(reasoning_effort)
    thinking_budget = _resolve_clamped_thinking_budget(model, options, params)

    # extra_body 单点累积：非标准字段统一从这里上线
    extra_body: Dict[str, Any] = {}

    # prompt cache（对齐 TS 顶层 prompt_cache_key/retention——Python 侧经 extra_body 上线）
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

    level_map = model.thinking_level_map or {}

    def _off_value() -> Optional[str]:
        off = level_map.get("off")
        return off if isinstance(off, str) else None

    def _off_is_explicitly_null() -> bool:
        return "off" in level_map and level_map["off"] is None

    if model.reasoning:
        if compat.thinking_format == "zai":
            extra_body["thinking"] = (
                {"type": "enabled", "clear_thinking": False}
                if reasoning_effort
                else {"type": "disabled"}
            )
            if reasoning_effort and compat.supports_reasoning_effort:
                effort = _map_level(level_map, reasoning_effort)
                if isinstance(effort, str):
                    params["reasoning_effort"] = effort
        elif compat.thinking_format == "qwen":
            extra_body["enable_thinking"] = bool(reasoning_effort)
            if reasoning_effort and compat.supports_reasoning_effort:
                effort = _map_level(level_map, reasoning_effort)
                if isinstance(effort, str):
                    params["reasoning_effort"] = effort
        elif compat.thinking_format == "qwen-chat-template":
            extra_body["chat_template_kwargs"] = {
                "enable_thinking": bool(reasoning_effort),
                "preserve_thinking": True,
            }
        elif compat.thinking_format == "chat-template":
            chat_kwargs = _build_chat_template_values(
                model,
                options,
                compat.chat_template_kwargs or {},
                thinking_budget,
                reasoning_effort,
            )
            if chat_kwargs:
                extra_body["chat_template_kwargs"] = chat_kwargs
        elif compat.thinking_format == "baseten":
            chat_args = _build_chat_template_values(
                model,
                options,
                compat.chat_template_args or {},
                thinking_budget,
                reasoning_effort,
            )
            if chat_args:
                extra_body["chat_template_args"] = chat_args
            if compat.supports_reasoning_effort:
                requested = reasoning_effort
                effort = (
                    _map_level(level_map, requested)
                    if requested
                    else level_map.get("off")
                )
                if isinstance(effort, str):
                    params["reasoning_effort"] = effort
        elif compat.thinking_format == "deepseek":
            if reasoning_effort:
                extra_body["thinking"] = {"type": "enabled"}
            elif not _off_is_explicitly_null():
                extra_body["thinking"] = {"type": "disabled"}
            if reasoning_effort and compat.supports_reasoning_effort:
                effort = _map_level(level_map, reasoning_effort)
                if isinstance(effort, str):
                    params["reasoning_effort"] = effort
        elif compat.thinking_format == "openrouter":
            # OpenRouter 用嵌套 reasoning 对象跨 provider 归一
            if reasoning_effort:
                effort = _map_level(level_map, reasoning_effort)
                if isinstance(effort, str):
                    extra_body["reasoning"] = {"effort": effort}
            elif not _off_is_explicitly_null():
                extra_body["reasoning"] = {"effort": _off_value() or "none"}
        elif compat.thinking_format == "ant-ling":
            if reasoning_effort and model.thinking_level_map:
                effort = model.thinking_level_map.get(reasoning_effort)
                if isinstance(effort, str):
                    extra_body["reasoning"] = {"effort": effort}
        elif compat.thinking_format == "together":
            extra_body["reasoning"] = {"enabled": bool(reasoning_effort)}
            if reasoning_effort and compat.supports_reasoning_effort:
                effort = _map_level(level_map, reasoning_effort)
                if isinstance(effort, str):
                    params["reasoning_effort"] = effort
        elif compat.thinking_format == "string-thinking":
            if reasoning_effort:
                effort = _map_level(level_map, reasoning_effort)
                if isinstance(effort, str):
                    extra_body["thinking"] = effort
            elif not _off_is_explicitly_null():
                extra_body["thinking"] = _off_value() or "none"
        elif reasoning_effort and compat.supports_reasoning_effort:
            # OpenAI 风格 reasoning_effort
            params["reasoning_effort"] = _map_level(level_map, reasoning_effort)
        elif not reasoning_effort and compat.supports_reasoning_effort:
            off_value = _off_value()
            if off_value is not None:
                params["reasoning_effort"] = off_value

    # 顶层思考预算字段（独立于 thinking_format：同一 server 可服务多种
    # 模型。reasoning 与答案共享 max_tokens，未封顶的推理阶段会吃光整个
    # 响应——无答案、无工具调用。）
    budget_field = _resolve_thinking_token_budget_field(compat)
    if budget_field and thinking_budget is not None:
        extra_body[budget_field] = thinking_budget

    # OpenRouter / Vercel 路由偏好：以模型自身 compat 为准（自定义网关/代理也可用）
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

    # 最后合并 sampling_params，让自定义键覆盖命名请求字段（对齐 TS）
    if options and options.sampling_params:
        params.update(options.sampling_params)

    return params
