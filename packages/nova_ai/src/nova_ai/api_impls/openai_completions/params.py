"""请求体参数构建（对齐 TS ``buildParams``，2026-08 终态）。

形状纪律（对齐 pi 的"SDK 类型打底 + as any 逃逸"）：

- 标准字段集中在 ``_ParamsBag`` TypedDict 声明——键名拼写在静态检查期
  就炸（SDK 官方 TypedDict 与 dict 形态的消息构造链摩擦大，故用自有
  声明，保护等级一致）；
- 非标准字段（``prompt_cache_key`` / ``tool_stream`` / ``thinking.*`` /
  ``chat_template_*`` / 思考预算字段 / ``reasoning_details`` 等）统一经
  ``extra_body`` 汇入线上 JSON 顶层——散键禁止直写 ``params["x"]``；
- ``sampling_params``（模型级自由负载）在出货边界才合入。
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypedDict, Union, cast

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from nova_protocol import (
    CacheRetention,
    Context,
    Model,
    OpenAICompletionsCompat,
    ThinkingFormat,
    ThinkingLevelMap,
)

from ...stream_options import StreamOptions
from .._shared.prompt_cache import (
    _apply_anthropic_cache_control,
    _get_compat_cache_control,
    clamp_openai_prompt_cache_key,
    resolve_cache_retention,
)
from .._shared.simple_options import (
    clamp_thinking_budget_to_answer_room,
    resolve_sampling_params,
    thinking_budget_for_level,
)
from .._shared.transcript import (
    normalize_context,
    resolve_transcript,
    resolve_transcript_tools,
)
from .compat import get_compat
from .messages import convert_messages, convert_tools, has_tool_history
from .options import ToolChoiceFunction

_OMIT = object()


class _ParamsBag(TypedDict, total=False):
    """``build_params`` 标准字段的声明形状（规则 10：声明不校验——键名
    拼写由静态检查执法；值在边界经 SDK 校验）。

    方言字段不进本袋，一律走 ``extra_body``（模块 docstring 纪律）。
    """

    model: str
    messages: List["ChatCompletionMessageParam"]  # convert_messages 产出
    stream: bool
    stream_options: Dict[str, Any]
    store: bool
    max_tokens: int
    max_completion_tokens: int
    temperature: float
    metadata: Dict[
        str, Any
    ]  # 比 SDK 的 Dict[str, str] 宽——与 StreamOptions.metadata 同形
    tools: List["ChatCompletionToolParam"]  # convert_tools 产出
    parallel_tool_calls: bool
    tool_choice: Union[str, ToolChoiceFunction]
    reasoning_effort: str
    # 比 SDK 的 ReasoningEffort Literal 宽：level_map 可映射出 "max" 等
    # SDK 尚未收录的级别（SDK 类型滞后于线，线上是自由字符串）
    extra_body: Dict[str, Any]


def _resolve_thinking_token_budget_field(
    compat: OpenAICompletionsCompat,
) -> Optional[str]:
    """解析顶层思考预算字段（显式字段名优先；对齐 TS resolveThinkingTokenBudgetField）。"""
    if compat.thinking_token_budget_field:
        return compat.thinking_token_budget_field
    if compat.supports_thinking_token_budget:
        return "thinking_token_budget"
    return None


def _map_level(
    level_map: ThinkingLevelMap, level: str, *, null_falls_back: bool = False
) -> Optional[str]:
    """映射思考级别。

    两种"不映射"必须区分——键缺失回落原值；显式 ``None`` 的语义按 format
    分派（对齐 TS）：zai/baseten/chat-template 省略字段（``None`` = 不支持）；
    qwen/deepseek/openrouter/together/string-thinking/openai-style 经
    ``null_falls_back=True`` 回落原始级别串（pi 的 `?? level` 对位）。
    """
    if level not in level_map:
        return level
    mapped = level_map[level]
    if mapped is None and null_falls_back:
        return level
    return mapped


def _resolve_clamped_thinking_budget(
    model: Model,
    options: Optional[StreamOptions],
    params: "_ParamsBag",
) -> Optional[int]:
    """按答案余量钳制思考预算（对齐 TS resolveClampedThinkingBudget）。"""
    # 专属字段经 getattr 防御（TS ?. 的 Python 对位）：ModelsManager 高层路径
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
    options: Optional[StreamOptions],
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
    options: Optional[StreamOptions],
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
    level_map: ThinkingLevelMap = model.thinking_level_map or {}
    key = reasoning_effort if reasoning_effort else "off"
    if key not in level_map:
        return reasoning_effort if reasoning_effort else _OMIT
    mapped = level_map.get(key)
    return mapped if isinstance(mapped, str) else _OMIT


def build_params(
    model: Model,
    context: Context,
    options: Optional[StreamOptions] = None,
    compat: Optional[OpenAICompletionsCompat] = None,
    cache_retention: Optional[CacheRetention] = None,
) -> Dict[str, Any]:
    """构建 OpenAI API 请求体参数（对齐 TS buildParams 终态）。

    返回的是厂商线上形状的开放 dict（自由负载语义）——标准字段 +
    ``extra_body`` 累积的非标准字段，直交 openai SDK。

    ``options`` 的实测契约是 ``StreamOptions`` 任意子类：ModelsManager 高层路径
    经 applyAuth 后传的是通用基类（不带 reasoning_effort 等协议专属字段），
    专属字段一律经 getattr 防御读取（TS ``?.`` 的 Python 对位）。
    """
    compat = compat or get_compat(model)
    cache_retention = cache_retention or resolve_cache_retention(
        options.cache_retention if options else None,
        options.env if options else None,
    )

    messages = convert_messages(model, context, compat)
    cache_control = _get_compat_cache_control(compat, cache_retention)

    params: _ParamsBag = {
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

    metadata = getattr(options, "metadata", None) if options else None
    if metadata:
        # 旧单体遗漏修复：OpenAI store:true 场景的轨迹追踪 metadata
        params["metadata"] = metadata

    # extra_body 单点累积：非标准字段统一从这里上线
    extra_body: Dict[str, Any] = {}

    # 工具声明分工（pi resolveTranscriptTools 对位）：additions 形态
    # （中途 system + 工具新增双旗标，Kimi K3）下顶层列表取最后一次非
    # 增量变更后的状态——移除/重定义无法就地表达，缓存前缀每次移除只
    # 失效一次；其后的新增经 kimi 工具型 system 消息就地锚定（归
    # convert_messages）。none 形态顶层即当前集。
    support = (
        "additions"
        if (
            compat.supports_mid_convo_system_messages
            and compat.supports_mid_convo_tool_additions
        )
        else "none"
    )
    transcript_tools = resolve_transcript_tools(
        resolve_transcript(
            normalize_context(context), compat.supports_mid_convo_system_messages
        ),
        support,
    )

    if transcript_tools.request_tools:
        params["tools"] = convert_tools(transcript_tools.request_tools, compat)
        if compat.zai_tool_stream:
            extra_body["tool_stream"] = True
        parallel_tool_calls = getattr(options, "parallel_tool_calls", None)
        if parallel_tool_calls is not None:
            params["parallel_tool_calls"] = parallel_tool_calls
    elif has_tool_history(context.messages):
        # Anthropic（经 LiteLLM/代理）要求对话含工具调用/结果时必须带 tools 参数
        params["tools"] = []

    if cache_control:
        # cache_control 要往工具 dict 里写 SDK 未声明的键——本文件明文的
        # "SDK 类型打底 + 逃逸"哲学：此处即逃逸点，cast 到可变映射形状
        _apply_anthropic_cache_control(
            messages,
            cast(Optional[List[Dict[str, Any]]], params.get("tools")),
            cache_control,
        )

    # tool_choice 是显式调用方指令（对齐 pi，无条件透传）：尤其 "none"
    # 在无工具请求里也有明确语义，不能因 tools 缺席被静默丢弃。
    tool_choice = getattr(options, "tool_choice", None)
    if tool_choice:
        params["tool_choice"] = tool_choice

    # vLLM 调度优先级（对齐 TS：compat.vllmPriority 非 None 才发顶层
    # priority——非标准字段，经 extra_body 上线）
    if compat.vllm_priority is not None:
        extra_body["priority"] = compat.vllm_priority

    # ---- 思考参数（按 thinking_format 分派；对齐 TS 分支序）----
    # "off" 入口归一为 None：pi 的 reasoningEffort 类型层不含 off
    # （stream_simple 已过滤），但本层类型是宽松 str——归一保证任何
    # 分支都不会把 "off" 泄漏给请求体。
    reasoning_effort = getattr(options, "reasoning_effort", None) if options else None
    if reasoning_effort == "off":
        reasoning_effort = None
    thinking_budget = _resolve_clamped_thinking_budget(model, options, params)

    # prompt cache（对齐 TS 顶层 prompt_cache_key/retention——Python 侧经 extra_body 上线）
    session_id = options.session_id if options else None
    # 三态裁决（对齐 pi 0f4d5e960 promptCacheKeyMode）：enabled 缓存激活即
    # 发（兼容代理）；disabled 永不；auto 维持启发式（openai 官方端点或
    # long 保留 + supportsLongCacheRetention）
    prompt_cache_key_mode = compat.prompt_cache_key_mode or "auto"
    supports_prompt_cache_key = prompt_cache_key_mode == "enabled" or (
        prompt_cache_key_mode == "auto"
        and (
            "api.openai.com" in model.base_url
            or (
                cache_retention == CacheRetention.LONG
                and compat.supports_long_cache_retention
            )
        )
    )
    use_prompt_cache_key = (
        session_id is not None
        and cache_retention != CacheRetention.NONE
        and supports_prompt_cache_key
    )
    if use_prompt_cache_key:
        extra_body["prompt_cache_key"] = clamp_openai_prompt_cache_key(session_id)
    if cache_retention == CacheRetention.LONG and compat.supports_long_cache_retention:
        extra_body["prompt_cache_retention"] = "24h"

    level_map: ThinkingLevelMap = model.thinking_level_map or {}

    def _off_value() -> Optional[str]:
        off = level_map.get("off")
        return off if isinstance(off, str) else None

    def _off_is_explicitly_null() -> bool:
        return "off" in level_map and level_map["off"] is None

    if model.reasoning:
        if compat.thinking_format == ThinkingFormat.ZAI:
            extra_body["thinking"] = (
                {"type": "enabled", "clear_thinking": False}
                if reasoning_effort
                else {"type": "disabled"}
            )
            if reasoning_effort and compat.supports_reasoning_effort:
                effort = _map_level(level_map, reasoning_effort)
                if isinstance(effort, str):
                    params["reasoning_effort"] = effort
        elif compat.thinking_format == ThinkingFormat.QWEN:
            extra_body["enable_thinking"] = bool(reasoning_effort)
            if reasoning_effort and compat.supports_reasoning_effort:
                effort = _map_level(level_map, reasoning_effort, null_falls_back=True)
                if isinstance(effort, str):
                    params["reasoning_effort"] = effort
        elif compat.thinking_format == ThinkingFormat.QWEN_CHAT_TEMPLATE:
            extra_body["chat_template_kwargs"] = {
                "enable_thinking": bool(reasoning_effort),
                "preserve_thinking": True,
            }
        elif compat.thinking_format == ThinkingFormat.CHAT_TEMPLATE:
            chat_kwargs = _build_chat_template_values(
                model,
                options,
                compat.chat_template_kwargs or {},
                thinking_budget,
                reasoning_effort,
            )
            if chat_kwargs:
                extra_body["chat_template_kwargs"] = chat_kwargs
        elif compat.thinking_format == ThinkingFormat.BASETEN:
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
        elif compat.thinking_format == ThinkingFormat.DEEPSEEK:
            if reasoning_effort:
                extra_body["thinking"] = {"type": "enabled"}
            elif not _off_is_explicitly_null():
                extra_body["thinking"] = {"type": "disabled"}
            if reasoning_effort and compat.supports_reasoning_effort:
                effort = _map_level(level_map, reasoning_effort, null_falls_back=True)
                if isinstance(effort, str):
                    params["reasoning_effort"] = effort
        elif compat.thinking_format == ThinkingFormat.OPENROUTER:
            # OpenRouter 用嵌套 reasoning 对象跨 provider 归一
            if reasoning_effort:
                effort = _map_level(level_map, reasoning_effort, null_falls_back=True)
                if isinstance(effort, str):
                    extra_body["reasoning"] = {"effort": effort}
            elif not _off_is_explicitly_null():
                extra_body["reasoning"] = {"effort": _off_value() or "none"}
        elif compat.thinking_format == ThinkingFormat.ANT_LING:
            if reasoning_effort and model.thinking_level_map:
                effort = model.thinking_level_map.get(reasoning_effort)
                if isinstance(effort, str):
                    extra_body["reasoning"] = {"effort": effort}
        elif compat.thinking_format == ThinkingFormat.TOGETHER:
            extra_body["reasoning"] = {"enabled": bool(reasoning_effort)}
            if reasoning_effort and compat.supports_reasoning_effort:
                effort = _map_level(level_map, reasoning_effort, null_falls_back=True)
                if isinstance(effort, str):
                    params["reasoning_effort"] = effort
        elif compat.thinking_format == ThinkingFormat.STRING_THINKING:
            if reasoning_effort:
                effort = _map_level(level_map, reasoning_effort, null_falls_back=True)
                if isinstance(effort, str):
                    extra_body["thinking"] = effort
            elif not _off_is_explicitly_null():
                extra_body["thinking"] = _off_value() or "none"
        elif reasoning_effort and compat.supports_reasoning_effort:
            # OpenAI 风格 reasoning_effort
            effort = _map_level(level_map, reasoning_effort, null_falls_back=True)
            if isinstance(effort, str):
                params["reasoning_effort"] = effort
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

    if extra_body:
        params["extra_body"] = extra_body

    # 最后合并采样参数（对齐 pi resolveSamplingParams）：模型默认 <
    # 吸附后的思考级别参数 < 请求级覆盖。direct stream() 与 stream_simple()
    # 共用这一出货点，不能只在 simple 包装层应用级别分层。
    final: Dict[str, Any] = dict(params)
    sampling_params = resolve_sampling_params(
        model,
        reasoning_effort or "off",
        options.sampling_params if options else None,
    )
    if sampling_params:
        final.update(sampling_params)

    return final
