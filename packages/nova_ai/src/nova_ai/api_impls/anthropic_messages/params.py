"""请求体参数构建（对齐 TS ``buildParams``）。

形状纪律同 openai_completions：标准字段集中在 ``_ParamsBag`` TypedDict
声明（键名拼写静态执法），值在出货边界由 SDK 校验。
"""

from typing import Any, Dict, List, NamedTuple, Optional, Tuple, TypedDict

from nova_protocol import CacheRetention, Context, Model, Tool

from ...stream_options import StreamOptions
from ...utils.surrogate import sanitize_surrogates
from .._shared.prompt_cache import CacheControl, resolve_cache_retention
from .._shared.transcript import (
    get_current_tools,
    get_initial_system_message,
    get_system_message_text,
    normalize_context,
    resolve_transcript,
    without_initial_system_message,
)
from .compat import ResolvedAnthropicCompat, get_compat
from .constrained import prefers_json_schema_strict_sampling
from .messages import ConvertedAnthropicMessages, convert_messages, convert_tools
from .options import AnthropicOptions

# OAuth 令牌的强制身份系统提示（对齐 TS buildParams 的 isOAuthToken 分支）
_CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# mid-convo effort 的两个 beta（对齐 TS）：输出配置中途变更 + 思考绑定控制
MID_CONVERSATION_OUTPUT_CONFIG_BETA = "mid-conversation-output-config-2026-07-01"
THINKING_BINDING_CONTROLS_BETA = "thinking-binding-controls-2026-08-01"

# INLINE_TOOLS beta（对齐 TS）：中途工具变更以 tool_addition/tool_removal
# 内容块按值下发，顶层工具列表固定
INLINE_TOOLS_BETA = "inline-tools-2026-09-15"

# INLINE_TOOLS 形态下常驻的稳定占位工具（对齐 TS DEFERRED_TOOL_PLACEHOLDER）：
# Anthropic 为中途工具变更注入隐藏提示脚手架——从首个请求就声明该占位，
# 脚手架留在缓存前缀内，首次工具变更不打爆缓存（实测：没有它全 miss）。
# 永不激活，模型不可见。
DEFERRED_TOOL_PLACEHOLDER: Dict[str, Any] = {
    "name": "__pi_deferred_placeholder__",
    "description": "Reserved placeholder. Never available. Never call this.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
    "defer_loading": True,
}


def resolve_native_tool_changes(
    context: Context, compat: ResolvedAnthropicCompat
) -> bool:
    """INLINE_TOOLS 形态判定（对齐 TS ``nativeToolChanges``）：中途 system
    消息 + 中途工具变更双旗标，且首条 system 消息携带初始工具集（无初始
    工具则占位工具无锚——Anthropic 拒绝全 defer 的列表，回退普通形态）。
    """
    if not (
        compat.supports_mid_convo_system_messages
        and compat.supports_mid_convo_tool_changes
    ):
        return False
    head = get_initial_system_message(normalize_context(context))
    return bool(head is not None and head.tools_added)


def is_thinking_disabled(
    model: Model,
    options: Optional[AnthropicOptions],
) -> bool:
    """显式关思考判定（对齐 TS ``thinkingDisabled``）：reasoning 模型 +
    ``thinking_enabled`` 显式 False + 端点支持 off（``thinking_level_map``
    的 ``"off"`` 未显式 null 化——显式 null = 不支持关，保持思考开启）。
    """
    thinking_enabled = getattr(options, "thinking_enabled", None) if options else None
    if not (model.reasoning and thinking_enabled is False):
        return False
    level_map = model.thinking_level_map or {}
    off_explicitly_null = "off" in level_map and level_map["off"] is None
    return not off_explicitly_null


def resolve_active_effort(
    model: Model,
    options: Optional[AnthropicOptions],
    compat: ResolvedAnthropicCompat,
) -> Optional[str]:
    """mid-convo effort 的当前档（对齐 TS activeEffort / effortMessages）。

    开启条件：模型 ``supports_mid_convo_effort`` 且思考未显式关闭（关闭时
    不可跨消息改 effort——pi 同款：off 不发标记）。返回 ``None`` = 本请求
    不发 effort 标记也不记录。
    """
    if not compat.supports_mid_convo_effort or is_thinking_disabled(model, options):
        return None
    return getattr(options, "effort", None) or "high"


def insert_thinking_level_messages(
    converted: ConvertedAnthropicMessages,
    active_effort: str,
) -> List[Dict[str, Any]]:
    """effort 标记注入（对齐 TS insertThinkingLevelMessages）：wire 索引
    命中的 assistant 前插一条 ``{role: system, output_config: {effort}}``
    （其 ``provider_thinking_level``），末尾追加当前档标记。

    档位记录归 ``convert_messages`` 的 wire 索引表（pi 同款——**无
    stop_reason 过滤**：TOOL_USE/LENGTH 回复在线上就在记录序列里；
    ERROR/ABORTED 被 transform 丢弃天然不上线）。
    """
    result: List[Dict[str, Any]] = []
    for index, message in enumerate(converted.messages):
        effort = converted.assistant_levels.get(index)
        if effort is not None:
            result.append(
                {
                    "role": "system",
                    "content": [],
                    "output_config": {"effort": effort},
                }
            )
        result.append(message)
    # 当前档收尾标记（对齐 TS activeEffort 末条）
    result.append(
        {"role": "system", "content": [], "output_config": {"effort": active_effort}}
    )
    return result


class _ParamsBag(TypedDict, total=False):
    """``build_params`` 标准字段的声明形状（规则 10：声明不校验）。"""

    model: str
    messages: List[Dict[str, Any]]
    max_tokens: int
    stream: bool
    system: List[Dict[str, Any]]
    temperature: float
    thinking: Dict[str, Any]
    output_config: Dict[str, Any]
    tools: List[Dict[str, Any]]
    tool_choice: Dict[str, Any]
    metadata: Dict[str, Any]
    fallbacks: List[Dict[str, str]]


def _resolve_cache_control(
    model: Model,
    cache_retention: CacheRetention,
    compat: ResolvedAnthropicCompat,
) -> Optional[CacheControl]:
    """解析 cache_control（对齐 TS getCacheControl）：none 关；long 且端点
    支持长保留时带 ttl=1h。"""
    if cache_retention == CacheRetention.NONE:
        return None
    control: CacheControl = {"type": "ephemeral"}
    if cache_retention == CacheRetention.LONG and compat.supports_long_cache_retention:
        control["ttl"] = "1h"
    return control


def build_params_with_meta(
    model: Model,
    context: Context,
    options: Optional[AnthropicOptions] = None,
    *,
    compat: Optional[ResolvedAnthropicCompat] = None,
    cache_retention: Optional[CacheRetention] = None,
    is_oauth: bool = False,
    disable_preferred_strict_tools: bool = False,
) -> Tuple[Dict[str, Any], bool]:
    """构建 Anthropic Messages 请求体参数 + strict 降级判定（对齐 TS
    buildParams 的 ``hasPreferredStrictTools`` 返回）。

    返回 ``(params, has_preferred_strict_tools)``；后者为真表示本请求
    携带 prefer-strict 工具——端点 400 报 strict 超限时调用方应置
    ``disable_preferred_strict_tools`` 重发（prefer 工具降级非严格）。
    """
    resolved_compat = compat or get_compat(model)
    retention = cache_retention or resolve_cache_retention(
        options.cache_retention if options else None,
        options.env if options else None,
    )
    cache_control = _resolve_cache_control(model, retention, resolved_compat)

    # 提示词与工具声明经 normalize/resolve 归一（pi transcript.ts 对位）：
    # system_prompt/tools 折成首条 system 消息；模型不接受中途 system
    # 消息时全部折叠回这一条（compat.supports_mid_convo_system_messages 门控）
    normalized = normalize_context(context)
    resolved = resolve_transcript(
        normalized, resolved_compat.supports_mid_convo_system_messages
    )
    head = get_initial_system_message(resolved)
    rest = without_initial_system_message(resolved)

    # INLINE_TOOLS 形态（对齐 TS nativeToolChanges）：中途工具变更以
    # tool_addition/tool_removal 内容块按值随消息下发（同名重定义天然可
    # 表达），顶层工具列表固定 = 初始工具 + 占位——缓存前缀存活
    initial_tools = list(head.tools_added or []) if head is not None else []
    native_tool_changes = (
        bool(resolved_compat.supports_mid_convo_system_messages)
        and bool(resolved_compat.supports_mid_convo_tool_changes)
        and len(initial_tools) > 0
    )

    # prefer-strict 追踪（对齐 TS convertToolDefinitions 闭包的
    # hasPreferredStrictTools——顶层与消息内 addition 都计入）
    preferred_strict_seen = False

    def _convert_tools_tracked(
        tools: List[Tool], tool_cache_control: Optional[CacheControl] = None
    ) -> List[Dict[str, Any]]:
        nonlocal preferred_strict_seen
        entries = convert_tools(
            tools,
            resolved_compat,
            tool_cache_control,
            disable_preferred_strict_tools,
        )
        for tool, entry in zip(tools, entries):
            if entry.get("strict") is True and prefers_json_schema_strict_sampling(
                tool
            ):
                preferred_strict_seen = True
        return entries

    # mid-convo effort 判定（对齐 TS buildParams 的次序：effortMessages
    # 先于 convertMessages——档位记录由转换期 wire 索引承载）
    thinking_disabled = is_thinking_disabled(model, options)
    # effort 标记消息形态（对齐 TS effortMessages）：关闭时不可跨消息改
    # effort，不发标记
    effort_messages = (
        resolved_compat.supports_mid_convo_effort and not thinking_disabled
    )
    active_effort = resolve_active_effort(model, options, resolved_compat)

    converted = convert_messages(
        model,
        context.model_copy(update={"messages": rest}),
        resolved_compat,
        cache_control,
        # INLINE 形态才注入定义转换器（对齐 TS：cache_control 只给顶层
        # 工具，消息内 addition 不带）
        _convert_tools_tracked if native_tool_changes else None,
        managed_provider=model.provider if effort_messages else None,
    )
    # 中途 system 消息：穿过模式时归 convert_messages 的 held-back 队列
    # 渲染（pi convertMessages 同款——下一个非空 assistant 前发出；折叠
    # 模式下 rest 中已无 system 消息）

    params: _ParamsBag = {
        "model": model.id,
        # effort 标记注入（对齐 TS：wire 索引表 + 当前档收尾——历史档含
        # TOOL_USE/LENGTH 回复，pi 同款无 stop 过滤）
        "messages": (
            insert_thinking_level_messages(converted, active_effort)
            if active_effort is not None
            else converted.messages
        ),
        # Anthropic 必填：缺省落模型上限（窗口钳制归 stream_simple）
        "max_tokens": (options.max_tokens if options and options.max_tokens else None)
        or model.max_tokens,
        "stream": True,
    }

    # system 顶层块（OAuth 必须先置 Claude Code 身份——对齐 TS）；
    # 提示词文本取自首条 system 消息（normalize 后 system_prompt 即在其中，
    # 折叠模式下亦含中途变更）
    system_blocks: List[Dict[str, Any]] = []
    if is_oauth:
        identity: Dict[str, Any] = {"type": "text", "text": _CLAUDE_CODE_IDENTITY}
        if cache_control:
            identity["cache_control"] = cache_control
        system_blocks.append(identity)
    head_text = get_system_message_text(head) if head is not None else ""
    if head_text:
        block: Dict[str, Any] = {
            "type": "text",
            "text": sanitize_surrogates(head_text),
        }
        if cache_control:
            block["cache_control"] = cache_control
        system_blocks.append(block)
    if system_blocks:
        params["system"] = system_blocks

    # 专属字段经 getattr 防御（TS ?. 对位）：高层路径可能传
    # StreamOptions 通用基类，不带协议专属字段
    thinking_enabled = getattr(options, "thinking_enabled", None) if options else None
    # temperature 与扩展思考不兼容（且 Opus 4.7+ 不支持——compat 裁决）；
    # mid-convo effort 模型同样不发（对齐 TS buildParams 的温度条件）
    if (
        options
        and options.temperature is not None
        and not thinking_enabled
        and not resolved_compat.supports_mid_convo_effort
        and resolved_compat.supports_temperature
    ):
        params["temperature"] = options.temperature

    # 工具（对齐 TS buildParams 双形态）
    tool_cache_control = (
        cache_control if resolved_compat.supports_cache_control_on_tools else None
    )
    if native_tool_changes:
        # INLINE 形态：初始工具带 cache 断点收尾 + 占位常驻——列表此后不变，
        # 后续工具经 tool_addition 按值下发、tool_removal 撤回
        params["tools"] = [
            *_convert_tools_tracked(initial_tools, tool_cache_control),
            DEFERRED_TOOL_PLACEHOLDER,
        ]
    else:
        # 普通形态：当前工具集（折叠归约后）原样下发
        current_tools = get_current_tools(resolved)
        if current_tools:
            params["tools"] = _convert_tools_tracked(current_tools, tool_cache_control)

    # 思考（四分支互斥——对齐 TS buildParams 分支序：disabled /
    # effortMessages / adaptive / 预算制；判定已前置——见 convert_messages
    # 调用前）
    display = (
        (getattr(options, "thinking_display", None) or "summarized")
        if options
        else "summarized"
    )

    if thinking_disabled:
        params["thinking"] = {"type": "disabled"}
    elif effort_messages:
        # managed effort 模型用 adaptive thinking——前缀失配可 drop 而非持久
        # 400（block_binding 归 thinking-binding-controls beta）。顶层 effort
        # 属缓存前缀故固定 low：组织 cap effort 的账户会拒绝任何高于 cap 的
        # 顶层档位（对齐 pi e3bc05e87）；真档位由逐条 effort 标记承载
        params["thinking"] = {
            "type": "adaptive",
            "display": display,
            "block_binding": {"prefix_mismatch_behavior": "drop_block"},
        }
        params["output_config"] = {"effort": "low"}
    elif model.reasoning and options is not None and thinking_enabled:
        if resolved_compat.force_adaptive_thinking:
            params["thinking"] = {"type": "adaptive", "display": display}
            effort = getattr(options, "effort", None)
            if effort:
                params["output_config"] = {"effort": effort}
        else:
            params["thinking"] = {
                "type": "enabled",
                "budget_tokens": getattr(options, "thinking_budget_tokens", None)
                or 1024,
                "display": display,
            }

    metadata = options.metadata if options else None
    if metadata:
        user_id = metadata.get("user_id")
        if isinstance(user_id, str):
            params["metadata"] = {"user_id": user_id}

    tool_choice = getattr(options, "tool_choice", None) if options else None
    if tool_choice:
        params["tool_choice"] = (
            {"type": tool_choice} if isinstance(tool_choice, str) else tool_choice
        )

    # 服务端 refusal fallback（对齐 TS buildParams 收尾）：名单非空才发——
    # 无许可目标时 Anthropic 拒收该字段
    if resolved_compat.allowed_fallback_models:
        params["fallbacks"] = [
            {"model": fallback.model}
            for fallback in resolved_compat.allowed_fallback_models
        ]

    final: Dict[str, Any] = dict(params)
    if options and options.sampling_params:
        # 出货边界合并模型级/请求级自由负载（对齐 TS）
        final.update(options.sampling_params)
    return final, preferred_strict_seen


def build_params(
    model: Model,
    context: Context,
    options: Optional[AnthropicOptions] = None,
    *,
    compat: Optional[ResolvedAnthropicCompat] = None,
    cache_retention: Optional[CacheRetention] = None,
    is_oauth: bool = False,
) -> Dict[str, Any]:
    """构建 Anthropic Messages 请求体参数（对齐 TS buildParams）。

    ``max_tokens`` 是 Anthropic 的必填字段：缺省落模型上限（窗口钳制归
    ``stream_simple`` 路径，本层不重复钳制——对齐 TS）。
    """
    params, _ = build_params_with_meta(
        model,
        context,
        options,
        compat=compat,
        cache_retention=cache_retention,
        is_oauth=is_oauth,
    )
    return params
