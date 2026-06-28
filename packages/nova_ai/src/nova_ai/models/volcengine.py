"""
OpenAI 模型定义
"""

from typing import Dict
from ..types.model import Model, ModelCost
from ..types.enums import KnownApi, KnownProvider, ThinkingFormat
from ..types.compat import OpenAICompletionsCompat


# OpenAI 模型定义
VOLCENGINE_MODELS = {
    "deepseek-v3-2-251201": Model(
        id="deepseek-v3-2-251201",
        name="Deepseek-v3-2",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.VOLCENGINE,
        base_url="https://ark.cn-beijing.volces.com/api/v3/",
        reasoning=True,
        thinking_level_map={"minimal": None, "low": None, "medium": None, "high": "high", "xhigh": "max"},
        input_types=["text"],
        cost=ModelCost(
            input=2.0,
            output=8.0,
            cache_read=0.5,
            cache_write=0.0
        ),
        context_window=131072,
        max_tokens=32768,
        compat=OpenAICompletionsCompat(
            requires_reasoning_content_on_assistant_messages=True,
            thinking_format=ThinkingFormat.DEEPSEEK,
        ),
    ),
    "deepseek-v4-flash-260425": Model(
        id="deepseek-v4-flash-260425",
        name="Deepseek-V4-Flash",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.VOLCENGINE,
        base_url="https://ark.cn-beijing.volces.com/api/v3/",
        reasoning=True,
        thinking_level_map={"minimal": None, "low": None, "medium": None, "high": "high", "xhigh": "max"},
        input_types=["text"],
        cost=ModelCost(
            input=1,
            output=2,
            cache_read=0.2,
            cache_write=0.0
        ),
        context_window=1047576,
        max_tokens=393216,
        compat=OpenAICompletionsCompat(
            requires_reasoning_content_on_assistant_messages=True,
            thinking_format=ThinkingFormat.DEEPSEEK,
        ),
    ),
    "deepseek-v4-pro-260425": Model(
        id="deepseek-v4-pro-260425",
        name="Deepseek-V4-Pro",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.VOLCENGINE,
        base_url="https://ark.cn-beijing.volces.com/api/v3/",
        reasoning=True,
        thinking_level_map={"minimal": None, "low": None, "medium": None, "high": "high", "xhigh": "max"},
        input_types=["text"],
        cost=ModelCost(
            input=12,
            output=24,
            cache_read=1,
            cache_write=0.0
        ),
        context_window=1047576,
        max_tokens=393216,
        compat=OpenAICompletionsCompat(
            requires_reasoning_content_on_assistant_messages=True,
            thinking_format=ThinkingFormat.DEEPSEEK,
        ),
    ),
}


def get_volcengine_model(model_id: str) -> Model:
    """通过ID获取VOLCENGINE模型"""
    if model_id not in VOLCENGINE_MODELS:
        raise KeyError(f"Volcengine model not found: {model_id}")
    return VOLCENGINE_MODELS[model_id]


def list_volcengine_models() -> Dict[str, Model]:
    """列出所有VOLCENGINE模型"""
    return VOLCENGINE_MODELS.copy()