"""
模型工具函数
与模型相关的业务逻辑
"""

from typing import List, Optional

from ..types.enums import ModelThinkingLevel, ThinkingLevel
from ..types.model import Cost, Model, Usage

EXTENDED_THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"]


def to_thinking_level(
    level: Optional[ModelThinkingLevel],
) -> Optional[ThinkingLevel]:
    """把状态侧级别转换为请求侧级别。

    ``OFF`` / ``None`` → ``None``（关闭思考，不发送 reasoning 参数）；
    其余按值转换。用于 Agent/loop 等状态到请求的边界。
    入参接受 ``ModelThinkingLevel`` / ``ThinkingLevel`` / 字符串值，
    非法值抛 ``ValueError``。
    """
    if level is None:
        return None
    normalized = ModelThinkingLevel(getattr(level, "value", level))
    if normalized == ModelThinkingLevel.OFF:
        return None
    return ThinkingLevel(normalized.value)


def get_supported_thinking_levels(model: Model) -> List[ModelThinkingLevel]:
    """
    获取模型支持的思考级别列表

    规则：
    - 模型不支持 reasoning 时，只返回 [ModelThinkingLevel.OFF]
    - thinking_level_map 中显式标记为 null 的级别不受支持
    - xhigh / max 默认不受支持，除非 thinking_level_map 中显式定义

    Args:
        model: 模型对象

    Returns:
        支持的思考级别列表
    """
    if not model.reasoning:
        return [ModelThinkingLevel.OFF]

    result = []
    for level in EXTENDED_THINKING_LEVELS:
        if model.thinking_level_map and level in model.thinking_level_map:
            mapped = model.thinking_level_map[level]
            if mapped is None:
                continue  # 显式标记为不支持

        if level in ("xhigh", "max"):
            if (
                model.thinking_level_map is None
                or level not in model.thinking_level_map
            ):
                continue  # xhigh/max 默认不支持，除非显式定义

        result.append(ModelThinkingLevel(level))

    return result


def clamp_thinking_level(model: Model, level: ModelThinkingLevel) -> ModelThinkingLevel:
    """把请求的思考级别吸附到模型支持的最近级别。

    规则：
    - 请求的级别受支持 → 原样返回
    - 否则先向更高级别找最近的支持项，找不到再向更低级别找
    - 模型不支持 reasoning 时恒返回 ``ModelThinkingLevel.OFF``

    Args:
        model: 模型对象
        level: 请求的思考级别

    Returns:
        吸附后的思考级别
    """
    available = get_supported_thinking_levels(model)
    if level in available:
        return level

    ordered = [ModelThinkingLevel(l) for l in EXTENDED_THINKING_LEVELS]
    if level not in ordered:
        return available[0] if available else ModelThinkingLevel.OFF
    requested_index = ordered.index(level)

    for candidate in ordered[requested_index:]:
        if candidate in available:
            return candidate
    for candidate in reversed(ordered[:requested_index]):
        if candidate in available:
            return candidate
    return available[0] if available else ModelThinkingLevel.OFF


def models_are_equal(a: Optional[Model], b: Optional[Model]) -> bool:
    """按 provider + id 判断两个模型是否等价（对齐 TS modelsAreEqual）。"""
    if a is None or b is None:
        return False
    return a.id == b.id and a.provider == b.provider


def has_api(model: Model, api: str) -> bool:
    """运行时检查 model.api 是否为指定 API（对齐 TS hasApi）。"""
    return model.api == api


def calculate_cost(model: Model, usage: Usage) -> Cost:
    """
    根据模型和用量计算成本（对齐 TS calculateCost，支持分层定价）。

    Args:
        model: 模型对象
        usage: 使用统计

    Returns:
        成本明细（直接修改并返回usage.cost）
    """
    input_tokens = usage.input + usage.cache_read + usage.cache_write
    rates = model.cost
    matched_threshold = -1
    for tier in model.cost.tiers or []:
        if (
            input_tokens > tier.input_tokens_above
            and tier.input_tokens_above > matched_threshold
        ):
            rates = tier
            matched_threshold = tier.input_tokens_above

    # Anthropic 1h cache write 按 2x base input 计费
    long_write = usage.cache_write_1h or 0
    short_write = usage.cache_write - long_write

    usage.cost.input = (rates.input / 1000000) * usage.input
    usage.cost.output = (rates.output / 1000000) * usage.output
    usage.cost.cache_read = (rates.cache_read / 1000000) * usage.cache_read
    usage.cost.cache_write = (
        rates.cache_write * short_write + rates.input * 2 * long_write
    ) / 1000000
    usage.cost.total = (
        usage.cost.input
        + usage.cost.output
        + usage.cost.cache_read
        + usage.cost.cache_write
    )

    return usage.cost
