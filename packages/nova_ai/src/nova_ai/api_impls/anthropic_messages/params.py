"""请求体参数构建（对齐 TS ``buildParams``）。

形状纪律同 openai_completions：标准字段集中在 ``_ParamsBag`` TypedDict
声明（键名拼写静态执法），值在出货边界由 SDK 校验。
"""

from typing import Any, Dict, List, Optional, TypedDict

from nova_protocol import CacheRetention, Context, Model

from ...stream_options import StreamOptions
from ...utils.surrogate import sanitize_surrogates
from .._shared.prompt_cache import CacheControl, resolve_cache_retention
from .compat import ResolvedAnthropicCompat, get_compat
from .messages import convert_messages, convert_tools, split_deferred_tools
from .options import AnthropicOptions

# OAuth 令牌的强制身份系统提示（对齐 TS buildParams 的 isOAuthToken 分支）
_CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


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
    resolved_compat = compat or get_compat(model)
    retention = cache_retention or resolve_cache_retention(
        options.cache_retention if options else None,
        options.env if options else None,
    )
    cache_control = _resolve_cache_control(model, retention, resolved_compat)

    # deferred tools 拆分（tool_reference 支持的端点才启用——对齐 TS）
    immediate_tools, deferred_tools = split_deferred_tools(
        context, resolved_compat.supports_tool_references
    )
    deferred_list = list(deferred_tools.values())
    if not immediate_tools and deferred_list:
        # 全部延迟 = 端点拿不到任何工具定义——回退全即刻（对齐 TS）
        immediate_tools, deferred_list = deferred_list, []
    deferred_names = set(deferred_tools.keys())

    messages = convert_messages(
        model, context, resolved_compat, cache_control, deferred_names
    )

    params: _ParamsBag = {
        "model": model.id,
        "messages": messages,
        # Anthropic 必填：缺省落模型上限（窗口钳制归 stream_simple）
        "max_tokens": (options.max_tokens if options and options.max_tokens else None)
        or model.max_tokens,
        "stream": True,
    }

    # system 顶层块（OAuth 必须先置 Claude Code 身份——对齐 TS）
    system_blocks: List[Dict[str, Any]] = []
    if is_oauth:
        identity: Dict[str, Any] = {"type": "text", "text": _CLAUDE_CODE_IDENTITY}
        if cache_control:
            identity["cache_control"] = cache_control
        system_blocks.append(identity)
    if context.system_prompt:
        block: Dict[str, Any] = {
            "type": "text",
            "text": sanitize_surrogates(context.system_prompt),
        }
        if cache_control:
            block["cache_control"] = cache_control
        system_blocks.append(block)
    if system_blocks:
        params["system"] = system_blocks

    # 专属字段经 getattr 防御（TS ?. 对位）：高层路径可能传
    # StreamOptions 通用基类，不带协议专属字段
    thinking_enabled = getattr(options, "thinking_enabled", None) if options else None
    # temperature 与扩展思考不兼容（且 Opus 4.7+ 不支持——compat 裁决）
    if (
        options
        and options.temperature is not None
        and not thinking_enabled
        and resolved_compat.supports_temperature
    ):
        params["temperature"] = options.temperature

    # 工具（即刻组带 cache_control 收尾；延迟组 defer_loading）
    tool_entries: List[Dict[str, Any]] = []
    if immediate_tools:
        tool_entries.extend(
            convert_tools(immediate_tools, resolved_compat, cache_control)
        )
    if deferred_list:
        tool_entries.extend(
            convert_tools(
                deferred_list,
                resolved_compat,
                # 延迟组不打 cache_control（对齐 TS：cacheControl 仅给即刻组）
                defer_loading=True,
            )
        )
    if tool_entries:
        params["tools"] = tool_entries

    # 思考（三种形态：adaptive / 预算制 / 显式关闭——对齐 TS 分支序）
    if model.reasoning and options is not None:
        if thinking_enabled:
            display = getattr(options, "thinking_display", None) or "summarized"
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
        elif thinking_enabled is False:
            # 显式关闭仅当端点支持关（level_map 显式 null 化 "off" = 不支持，
            # 对齐 TS `thinkingLevelMap?.off !== null` 判定）
            level_map = model.thinking_level_map or {}
            off_explicitly_null = "off" in level_map and level_map["off"] is None
            if not off_explicitly_null:
                params["thinking"] = {"type": "disabled"}

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

    final: Dict[str, Any] = dict(params)
    if options and options.sampling_params:
        # 出货边界合并模型级/请求级自由负载（对齐 TS）
        final.update(options.sampling_params)
    return final
