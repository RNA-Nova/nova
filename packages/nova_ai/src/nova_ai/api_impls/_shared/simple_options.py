"""
流选项工具函数
处理模型请求的选项配置
"""

from typing import Any, Dict, Optional, Union

from nova_protocol import Context, Model, ModelThinkingLevel, ThinkingLevel

from ...stream_options import SimpleStreamOptions, StreamOptions, ThinkingBudgets
from ...utils.estimate import CONTEXT_SAFETY_TOKENS, estimate_context_tokens
from ...utils.model_utils import clamp_thinking_level

MIN_MAX_TOKENS = 1

# OpenRouter 用自己的 tokenizer 计输入，input + max_tokens 超窗即拒——
# 实测散文输入高估达 12%（对齐 pi 0a4fad548 #11712）
CONTEXT_SAFETY_RATIO = 0.15
# 比例余量仅在仍留出这么多输出时保留；否则（大窗快满/小窗本地模型）
# 只留固定余量，输出封顶该值——高估也能撑到压缩前
MIN_OUTPUT_WITH_RATIO_MARGIN = 32768


def clamp_max_tokens_to_context(model: Model, context: Context, max_tokens: int) -> int:
    """把 max_tokens 钳制到上下文剩余窗口内（对齐 TS clampMaxTokensToContext，
    含 0a4fad548 的比例余量）。

    余量 = max(固定 4096, 输入估算 × 15%)——给输入被高估留空间；
    余量形态下输出不足 32768 时退固定余量并把输出封顶 32768；
    context_window 无效（<=0）时只做下限保护。
    """
    if model.context_window <= 0:
        return max(MIN_MAX_TOKENS, max_tokens)
    input_tokens = estimate_context_tokens(context).tokens
    margin = max(
        CONTEXT_SAFETY_TOKENS, int(-(-input_tokens * CONTEXT_SAFETY_RATIO // 1))
    )
    room = model.context_window - input_tokens - margin
    if room < MIN_OUTPUT_WITH_RATIO_MARGIN:
        room = min(
            MIN_OUTPUT_WITH_RATIO_MARGIN,
            model.context_window - input_tokens - CONTEXT_SAFETY_TOKENS,
        )
    return min(max_tokens, max(MIN_MAX_TOKENS, room))


def resolve_sampling_params(
    model: Model,
    thinking_level: Union[ThinkingLevel, str, None],
    request_params: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """模型默认 < 吸附后的思考级别参数 < 请求参数（pi 同名函数对位）。"""
    raw_level = (
        thinking_level.value
        if isinstance(thinking_level, ThinkingLevel)
        else thinking_level
    )
    effective = clamp_thinking_level(
        model, ModelThinkingLevel(raw_level or ModelThinkingLevel.OFF.value)
    )
    level_params = (model.sampling_params_by_thinking_level or {}).get(effective.value)
    if model.sampling_params or level_params or request_params:
        return {
            **(model.sampling_params or {}),
            **(level_params or {}),
            **(request_params or {}),
        }
    return None


def build_base_options(
    model: Model,
    context: Context,
    options: Optional[SimpleStreamOptions] = None,
    api_key: Optional[str] = None,
) -> StreamOptions:
    """构建 provider-neutral 基础选项。"""
    sampling_params = resolve_sampling_params(
        model,
        options.reasoning if options else None,
        options.sampling_params if options else None,
    )
    requested_max_tokens = (
        options.max_tokens
        if options and options.max_tokens is not None
        else model.max_tokens
    )
    return StreamOptions(
        temperature=options.temperature if options else None,
        max_tokens=clamp_max_tokens_to_context(model, context, requested_max_tokens),
        signal=options.signal if options else None,
        api_key=api_key or (options.api_key if options else None),
        transport=options.transport if options else None,
        cache_retention=options.cache_retention if options else None,
        session_id=options.session_id if options else None,
        disable_continuation=options.disable_continuation if options else None,
        headers=options.headers if options else None,
        env=options.env if options else None,
        on_payload=options.on_payload if options else None,
        on_response=options.on_response if options else None,
        on_provider_stream_event=(
            options.on_provider_stream_event if options else None
        ),
        metadata=options.metadata if options else None,
        timeout=options.timeout if options else None,
        max_retries=options.max_retries if options else None,
        max_retry_delay_ms=options.max_retry_delay_ms if options else None,
        sampling_params=sampling_params,
        tool_choice=options.tool_choice if options else None,
    )


# ---------------------------------------------------------------------------
# 思考预算数学（对齐 TS simple-options.ts 的 budget 段）——vLLM/Qwen-SGLang/
# llama.cpp 这类 reasoning 与答案共享 max_tokens 的端点必需，否则未封顶的
# 推理阶段会吃光整个响应（无答案、无工具调用）。
# ---------------------------------------------------------------------------

# 共享响应上限下始终留给答案的 token 数
MIN_ANSWER_TOKENS = 1024

# 默认思考预算表（自定义表覆盖；对齐 TS）
_DEFAULT_THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
}


def clamp_reasoning(
    effort: Union[ThinkingLevel, str, None],
) -> Union[ThinkingLevel, str, None]:
    """把 xhigh/max 降为 high（预算表只到 high；对齐 TS clampReasoning）。"""
    # ThinkingLevel 是 str 枚举：成员比较对裸字符串输入同样命中
    if effort in (ThinkingLevel.XHIGH, ThinkingLevel.MAX):
        return "high"
    return effort


def thinking_budget_for_level(
    reasoning_level: Union[ThinkingLevel, str, None],
    custom_budgets: Optional[ThinkingBudgets] = None,
) -> int:
    """按思考级别取 token 预算（自定义表覆盖默认表；对齐 TS thinkingBudgetForLevel）。"""
    budgets = dict(_DEFAULT_THINKING_BUDGETS)
    if custom_budgets is not None:
        budgets.update(
            {
                k: v
                for k, v in {
                    "minimal": custom_budgets.minimal,
                    "low": custom_budgets.low,
                    "medium": custom_budgets.medium,
                    "high": custom_budgets.high,
                }.items()
                if v is not None
            }
        )
    level = clamp_reasoning(reasoning_level)
    # str 枚举与字符串键天然互命中（ThinkingLevel 继承 str）
    key = level.value if isinstance(level, ThinkingLevel) else level
    value = budgets.get(key) if key else None
    if value is None:
        value = budgets["medium"]
    return int(value)


def clamp_thinking_budget_to_answer_room(thinking_budget: int, ceiling: int) -> int:
    """钳制思考预算，保证共享上限下至少留 MIN_ANSWER_TOKENS 给答案。"""
    return min(int(thinking_budget), max(0, int(ceiling) - MIN_ANSWER_TOKENS))


def adjust_max_tokens_for_thinking(
    base_max_tokens: Optional[int],
    model_max_tokens: int,
    reasoning_level,
    custom_budgets: Optional[ThinkingBudgets] = None,
) -> "tuple[int, int]":
    """思考开启时的 (max_tokens, thinking_budget) 联合调整（对齐 TS
    ``adjustMaxTokensForThinking``）。

    调用方未设上限时用模型上限、思考预算装进上限内；设了上限则给思考
    预留预算空间（base + budget，不超模型上限）；预算再按答案余量钳制。
    """
    thinking_budget = thinking_budget_for_level(reasoning_level, custom_budgets)
    max_tokens = (
        model_max_tokens
        if base_max_tokens is None
        else min(base_max_tokens + thinking_budget, model_max_tokens)
    )
    if max_tokens <= thinking_budget:
        thinking_budget = clamp_thinking_budget_to_answer_room(
            thinking_budget, max_tokens
        )
    return max_tokens, thinking_budget
