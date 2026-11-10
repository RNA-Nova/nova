"""OpenRouter 实际账单字段的边界归一与总额覆盖（pi ``openrouter-cost.ts``）。"""

import math
from typing import Any

from nova_protocol import Model, Usage


def _field(value: Any, name: str) -> Any:
    """读取 SDK extra 残余（dict/对象两形态；仅此边界使用 duck-read）。"""
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _valid_cost(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def apply_openrouter_reported_cost(usage: Usage, reported: Any, model: Model) -> None:
    """当 OpenRouter 给出完整有效账单时，用实际费用覆盖目录估算总额。"""
    if model.provider != "openrouter" and "openrouter.ai" not in model.base_url:
        return
    reported_cost = _field(reported, "cost")
    if not _valid_cost(reported_cost):
        return
    is_byok = _field(reported, "is_byok")
    if is_byok is False:
        usage.cost.total = reported_cost
        usage.cost.total_from_provider = True
        return
    if is_byok is not True:
        return

    details = _field(reported, "cost_details")
    if details is None:
        return
    upstream = _field(details, "upstream_inference_cost")
    if upstream is None:
        prompt = _field(details, "upstream_inference_prompt_cost")
        completion = _field(details, "upstream_inference_completions_cost")
        if not _valid_cost(prompt) or not _valid_cost(completion):
            return
        upstream = prompt + completion
    if not _valid_cost(upstream):
        return
    usage.cost.total = reported_cost + upstream
    usage.cost.total_from_provider = True


__all__ = ["apply_openrouter_reported_cost"]
