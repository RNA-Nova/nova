"""Kimi Coding 模型定义。

模型目录对齐 TS ``providers/kimi-coding.models.ts``（auto-generated），
两点协议层差异：

- base_url 为 ``https://api.kimi.com/coding/v1``：Python 侧走
  openai-completions 实现（TS 侧为 anthropic-messages，后者不带 /v1）。
- compat 使用 ``OpenAICompletionsCompat`` 而非 anthropic compat；
  ``thinking_level_map`` 按 openai-completions 的 ``reasoning_effort``
  线值配置。
"""

from typing import Dict

from ...types.compat import OpenAICompletionsCompat
from ...types.enums import KnownApi, KnownProvider, ThinkingFormat
from ...types.model import Model, ModelCost

KIMI_CODING_BASE_URL = "https://api.kimi.com/coding/v1"

# 对齐 TS：endpoint 按 UA 识别 Kimi CLI 客户端
_KIMI_CLI_HEADERS = {"User-Agent": "KimiCLI/1.5"}

_K2_THINKING_MAP = {
    "off": None,
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "max",
}

_OPENAI_COMPAT = OpenAICompletionsCompat(
    thinking_format=ThinkingFormat.OPENAI,
    supports_reasoning_effort=True,
    supports_store=False,
    max_tokens_field="max_tokens",
    # kimi coding API 只接受 system 角色（reasoning 模型也不接受 developer）
    supports_developer_role=False,
)

KIMI_CODING_MODELS = {
    "k2p7": Model(
        id="k2p7",
        name="Kimi K2.7 Code",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.KIMI_CODING,
        base_url=KIMI_CODING_BASE_URL,
        reasoning=True,
        thinking_level_map=dict(_K2_THINKING_MAP),
        input_types=["text", "image"],
        cost=ModelCost(),
        context_window=262_144,
        max_tokens=32768,
        headers=dict(_KIMI_CLI_HEADERS),
        compat=_OPENAI_COMPAT.model_copy(),
    ),
    "k3": Model(
        id="k3",
        name="Kimi K3",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.KIMI_CODING,
        base_url=KIMI_CODING_BASE_URL,
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
        cost=ModelCost(),
        context_window=1_048_576,
        max_tokens=131_072,
        headers=dict(_KIMI_CLI_HEADERS),
        compat=_OPENAI_COMPAT.model_copy(),
    ),
    "kimi-for-coding": Model(
        id="kimi-for-coding",
        name="Kimi For Coding",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.KIMI_CODING,
        base_url=KIMI_CODING_BASE_URL,
        reasoning=True,
        thinking_level_map=dict(_K2_THINKING_MAP),
        input_types=["text", "image"],
        cost=ModelCost(),
        context_window=262_144,
        max_tokens=32768,
        headers=dict(_KIMI_CLI_HEADERS),
        compat=_OPENAI_COMPAT.model_copy(),
    ),
    "kimi-for-coding-highspeed": Model(
        id="kimi-for-coding-highspeed",
        name="Kimi For Coding HighSpeed",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.KIMI_CODING,
        base_url=KIMI_CODING_BASE_URL,
        reasoning=True,
        thinking_level_map=dict(_K2_THINKING_MAP),
        input_types=["text", "image"],
        cost=ModelCost(),
        context_window=262_144,
        max_tokens=32768,
        headers=dict(_KIMI_CLI_HEADERS),
        compat=_OPENAI_COMPAT.model_copy(),
    ),
    "kimi-k2-thinking": Model(
        id="kimi-k2-thinking",
        name="Kimi K2 Thinking",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.KIMI_CODING,
        base_url=KIMI_CODING_BASE_URL,
        reasoning=True,
        thinking_level_map=dict(_K2_THINKING_MAP),
        input_types=["text"],
        cost=ModelCost(),
        context_window=262_144,
        max_tokens=32768,
        headers=dict(_KIMI_CLI_HEADERS),
        compat=_OPENAI_COMPAT.model_copy(),
    ),
}


def get_kimi_coding_model(model_id: str) -> Model:
    """通过 ID 获取 Kimi Coding 模型。"""
    if model_id not in KIMI_CODING_MODELS:
        raise KeyError(f"Kimi Coding model not found: {model_id}")
    return KIMI_CODING_MODELS[model_id]


def list_kimi_coding_models() -> Dict[str, Model]:
    """列出所有 Kimi Coding 模型。"""
    return KIMI_CODING_MODELS.copy()
