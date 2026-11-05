"""Volcengine 的构建期知识（以真实 API 实测为准）。

deepseek 系思考映射与回放语义 + 钉住模型（数据源缺失但端点实测在售）+
方舟在售生命周期筛选（Shutdown/Retiring 剔除）。
"""

from typing import Dict, List

from nova_protocol import KnownApi, ModelFields

from ..define import define_provider

_PINNED_MODELS = {
    # 数据源缺失，但端点实测在售——对齐 TS "authoritative values" 模式
    "deepseek-v4-flash-260425": {
        "id": "deepseek-v4-flash-260425",
        "name": "Deepseek-V4-Flash",
        "reasoning": True,
        "input_types": ["text"],
        "cost": {"input": 1, "output": 2, "cache_read": 0.2, "cache_write": 0.0},
        "context_window": 1048576,
        "max_tokens": 393216,
    },
    "deepseek-v4-pro-260425": {
        "id": "deepseek-v4-pro-260425",
        "name": "Deepseek-V4-Pro",
        "reasoning": True,
        "input_types": ["text"],
        "cost": {"input": 12, "output": 24, "cache_read": 1, "cache_write": 0.0},
        "context_window": 1048576,
        "max_tokens": 393216,
    },
}


def _ark_lifecycle_filter(models: List["ModelFields"], ctx) -> None:
    """ark 生命周期筛选（跨模型逃生口）：显式坏状态（Shutdown/Retiring）
    整行剔除；**钉住模型豁免**（数据源缺失但端点实测在售——对齐
    TS "authoritative values" 模式，ark 状态表误伤它们）。无状态表
    （无 key）照常放行——构建期与运行期同一套规则。"""
    if ctx.ark_status:
        models[:] = [
            m
            for m in models
            if m.get("id") not in ctx.ark_status or m.get("id") in _PINNED_MODELS
        ]


DEF = define_provider(
    id="volcengine",
    name="Volcengine",
    base_url="https://ark.cn-beijing.volces.com/api/v3/",
    api=KnownApi.OPENAI_COMPLETIONS,
    # 方舟目录语义是"在售"——models.dev 标 deprecated 的条目与在售
    # 语义矛盾，剔除（ark 生命周期源兜底，不同于 pi 各家的缺省保留）
    skip_deprecated=True,
    # 方舟在售生命周期筛选（跨模型逃生口——私货归钩，不占公共字段）
    finalize_all=_ark_lifecycle_filter,
    model_corrections={
        "deepseek-v4-*": {
            # 活探（2026-11-02，ark 真机）：v4 系 minimal~max 全档 + off
            # （"none"）全通；bogus/off 报错——models.dev 的列表
            # [minimal,low,medium,high] 不全，以实测为准
            "thinking_level_map": {
                "off": "none",
                "minimal": "minimal",
                "low": "low",
                "medium": "medium",
                "high": "high",
                "xhigh": "xhigh",
                "max": "max",
            },
            "compat": {
                "requires_reasoning_content_on_assistant_messages": True,
                "thinking_format": "deepseek",
            },
        },
        # doubao-seed-character：models.dev 的 output 上限（256000）超出方舟真实
        # 上限 32768（实测 400："expected a value <= 32768"），修正为实测值
        "doubao-seed-character-*": {
            "max_tokens": 32768,
        },
    },
    pinned_models=_PINNED_MODELS,
)
