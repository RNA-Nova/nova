"""
模型工具函数
与模型相关的业务逻辑
"""

from typing import List
from ..types.model import Usage, Cost
from ..types.model import Model


EXTENDED_THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh"]


def get_supported_thinking_levels(model: Model) -> List[str]:
    """
    获取模型支持的思考级别列表

    规则：
    - 模型不支持 reasoning 时，只返回 ["off"]
    - thinking_level_map 中显式标记为 null 的级别不受支持
    - xhigh 默认不受支持，除非 thinking_level_map 中显式定义

    Args:
        model: 模型对象

    Returns:
        支持的思考级别列表
    """
    if not model.reasoning:
        return ["off"]

    result = []
    for level in EXTENDED_THINKING_LEVELS:
        if model.thinking_level_map and level in model.thinking_level_map:
            mapped = model.thinking_level_map[level]
            if mapped is None:
                continue  # 显式标记为不支持

        if level == "xhigh":
            if model.thinking_level_map is None or "xhigh" not in model.thinking_level_map:
                continue  # xhigh 默认不支持，除非显式定义

        result.append(level)

    return result


def calculate_cost(model: Model, usage: Usage) -> Cost:
    """
    根据模型和用量计算成本

    Args:
        model: 模型对象
        usage: 使用统计

    Returns:
        成本明细（直接修改并返回usage.cost）
    """
    # 成本计算：模型成本是$/M tokens，需要除以1,000,000得到每token成本
    usage.cost.input = (model.cost.input / 1000000) * usage.input
    usage.cost.output = (model.cost.output / 1000000) * usage.output
    usage.cost.cache_read = (model.cost.cache_read / 1000000) * usage.cache_read
    usage.cost.cache_write = (model.cost.cache_write / 1000000) * usage.cache_write
    usage.cost.total = (
        usage.cost.input
        + usage.cost.output
        + usage.cost.cache_read
        + usage.cost.cache_write
    )

    return usage.cost


def supports_xhigh_thinking(model: Model) -> bool:
    """
    检查模型是否支持xhigh思考级别

    当前支持的模型:
    - GPT-5.2 / GPT-5.3 模型家族
    - Anthropic Messages API Opus 4.6 模型 (xhigh 映射到 adaptive effort "max")

    Args:
        model: 模型对象

    Returns:
        是否支持xhigh思考级别
    """
    # GPT-5.2 / GPT-5.3 系列
    if "gpt-5.2" in model.id or "gpt-5.3" in model.id:
        return True

    # Anthropic Opus 4.6 系列
    if model.api == "anthropic-messages":
        return "opus-4-6" in model.id or "opus-4.6" in model.id

    return False
