"""Moonshot AI 模型定义。

参考 Kimi API 文档：
- base_url: https://api.moonshot.ai/v1
- 兼容 OpenAI Completions API
- 认证头: Authorization: Bearer $MOONSHOT_API_KEY
"""

from typing import Dict

from ...types.compat import OpenAICompletionsCompat
from ...types.enums import KnownApi, KnownProvider, ThinkingFormat
from ...types.model import Model, ModelCost

MOONSHOTAI_BASE_URL = "https://api.moonshot.ai/v1"

MOONSHOTAI_MODELS = {
    "kimi-k2-0711-preview": Model(
        id="kimi-k2-0711-preview",
        name="Kimi K2 0711",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.MOONSHOTAI,
        base_url=MOONSHOTAI_BASE_URL,
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0.6, output=2.5, cache_read=0.15),
        context_window=131072,
        max_tokens=16384,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.DEEPSEEK,
            supports_store=False,
            supports_developer_role=False,
            supports_reasoning_effort=False,
            supports_strict_mode=False,
            max_tokens_field="max_tokens",
        ),
    ),
    "kimi-k2-0905-preview": Model(
        id="kimi-k2-0905-preview",
        name="Kimi K2 0905",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.MOONSHOTAI,
        base_url=MOONSHOTAI_BASE_URL,
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0.6, output=2.5, cache_read=0.15),
        context_window=262144,
        max_tokens=262144,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.DEEPSEEK,
            supports_store=False,
            supports_developer_role=False,
            supports_reasoning_effort=False,
            supports_strict_mode=False,
            max_tokens_field="max_tokens",
        ),
    ),
    "kimi-k2-thinking": Model(
        id="kimi-k2-thinking",
        name="Kimi K2 Thinking",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.MOONSHOTAI,
        base_url=MOONSHOTAI_BASE_URL,
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=0.6, output=2.5, cache_read=0.15),
        context_window=262144,
        max_tokens=262144,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.DEEPSEEK,
            supports_store=False,
            supports_developer_role=False,
            supports_reasoning_effort=False,
            supports_strict_mode=False,
            max_tokens_field="max_tokens",
        ),
    ),
    "kimi-k2-thinking-turbo": Model(
        id="kimi-k2-thinking-turbo",
        name="Kimi K2 Thinking Turbo",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.MOONSHOTAI,
        base_url=MOONSHOTAI_BASE_URL,
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=1.15, output=8.0, cache_read=0.15),
        context_window=262144,
        max_tokens=262144,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.DEEPSEEK,
            supports_store=False,
            supports_developer_role=False,
            supports_reasoning_effort=False,
            supports_strict_mode=False,
            max_tokens_field="max_tokens",
        ),
    ),
    "kimi-k2-turbo-preview": Model(
        id="kimi-k2-turbo-preview",
        name="Kimi K2 Turbo",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.MOONSHOTAI,
        base_url=MOONSHOTAI_BASE_URL,
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=2.4, output=10.0, cache_read=0.6),
        context_window=262144,
        max_tokens=262144,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.DEEPSEEK,
            supports_store=False,
            supports_developer_role=False,
            supports_reasoning_effort=False,
            supports_strict_mode=False,
            max_tokens_field="max_tokens",
        ),
    ),
    "kimi-k2.5": Model(
        id="kimi-k2.5",
        name="Kimi K2.5",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.MOONSHOTAI,
        base_url=MOONSHOTAI_BASE_URL,
        reasoning=True,
        input_types=["text", "image"],
        cost=ModelCost(input=0.6, output=3.0, cache_read=0.1),
        context_window=262144,
        max_tokens=262144,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.DEEPSEEK,
            supports_store=False,
            supports_developer_role=False,
            supports_reasoning_effort=False,
            supports_strict_mode=False,
            max_tokens_field="max_tokens",
        ),
    ),
    "kimi-k2.6": Model(
        id="kimi-k2.6",
        name="Kimi K2.6",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.MOONSHOTAI,
        base_url=MOONSHOTAI_BASE_URL,
        reasoning=True,
        input_types=["text", "image"],
        cost=ModelCost(input=0.95, output=4.0, cache_read=0.16),
        context_window=262144,
        max_tokens=262144,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.DEEPSEEK,
            supports_store=False,
            supports_developer_role=False,
            supports_reasoning_effort=False,
            supports_strict_mode=False,
            max_tokens_field="max_tokens",
        ),
    ),
    "kimi-k2.7-code": Model(
        id="kimi-k2.7-code",
        name="Kimi K2.7 Code",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.MOONSHOTAI,
        base_url=MOONSHOTAI_BASE_URL,
        reasoning=True,
        thinking_level_map={"off": None},
        input_types=["text", "image"],
        cost=ModelCost(input=0.95, output=4.0, cache_read=0.19),
        context_window=262144,
        max_tokens=262144,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.DEEPSEEK,
            supports_store=False,
            supports_developer_role=False,
            supports_reasoning_effort=False,
            supports_strict_mode=False,
            max_tokens_field="max_tokens",
        ),
    ),
    "kimi-k2.7-code-highspeed": Model(
        id="kimi-k2.7-code-highspeed",
        name="Kimi K2.7 Code HighSpeed",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.MOONSHOTAI,
        base_url=MOONSHOTAI_BASE_URL,
        reasoning=True,
        thinking_level_map={"off": None},
        input_types=["text", "image"],
        cost=ModelCost(input=1.9, output=8.0, cache_read=0.38),
        context_window=262144,
        max_tokens=262144,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.DEEPSEEK,
            supports_store=False,
            supports_developer_role=False,
            supports_reasoning_effort=False,
            supports_strict_mode=False,
            max_tokens_field="max_tokens",
        ),
    ),
    "kimi-k3": Model(
        id="kimi-k3",
        name="Kimi K3",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.MOONSHOTAI,
        base_url=MOONSHOTAI_BASE_URL,
        reasoning=True,
        # k3 当前支持 low/high/max（TS 静态目录滞后，以真实 API 为准）
        thinking_level_map={
            "off": None,
            "minimal": None,
            "low": "low",
            "medium": None,
            "high": "high",
            "xhigh": None,
            "max": "max",
        },
        input_types=["text", "image"],
        cost=ModelCost(input=3.0, output=15.0, cache_read=0.3),
        context_window=1_048_576,
        max_tokens=131072,
        compat=OpenAICompletionsCompat(
            thinking_format=ThinkingFormat.DEEPSEEK,
            supports_store=False,
            supports_developer_role=False,
            supports_reasoning_effort=False,
            supports_strict_mode=False,
            max_tokens_field="max_tokens",
            requires_reasoning_content_on_assistant_messages=True,
            deferred_tools_mode="kimi",
        ),
    ),
}


def get_moonshotai_model(model_id: str) -> Model:
    """通过 ID 获取 Moonshot AI 模型。"""
    if model_id not in MOONSHOTAI_MODELS:
        raise KeyError(f"Moonshot AI model not found: {model_id}")
    return MOONSHOTAI_MODELS[model_id]


def list_moonshotai_models() -> Dict[str, Model]:
    """列出所有 Moonshot AI 模型。"""
    return MOONSHOTAI_MODELS.copy()
