"""
基础模型类型定义
包含模型定义、模型成本、用量统计
"""

from typing import Dict, List, Literal, Optional, Union

from pydantic import Field, model_validator

from .base_model import NovaBaseModel
from .compat import (
    AnthropicMessagesCompat,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
)
from .enums import Api, KnownApi, ProviderId, ThinkingLevelMap

_COMPAT_CLASS_BY_API = {
    KnownApi.OPENAI_COMPLETIONS.value: OpenAICompletionsCompat,
    KnownApi.OPENAI_RESPONSES.value: OpenAIResponsesCompat,
    KnownApi.ANTHROPIC_MESSAGES.value: AnthropicMessagesCompat,
}
"""api → compat 类的显式判别映射（与 ``Model._set_default_compat`` 的默认选择一致）。"""


class ModelCostRates(NovaBaseModel):
    """模型成本费率（$/百万tokens）"""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


class ModelCostTier(ModelCostRates):
    """分层定价：总输入超过该阈值时使用本费率"""

    input_tokens_above: int = 0


class ModelCost(ModelCostRates):
    """模型成本（$/百万tokens），支持分层定价"""

    tiers: Optional[List[ModelCostTier]] = None


class Model(NovaBaseModel):
    """模型定义"""

    id: str
    name: str
    api: Api
    provider: ProviderId
    base_url: str
    reasoning: bool
    thinking_level_map: Optional[ThinkingLevelMap] = None
    input_types: List[Literal["text", "image"]]
    cost: ModelCost
    context_window: int
    max_tokens: int
    headers: Optional[Dict[str, str]] = None
    compat: Optional[
        Union[OpenAICompletionsCompat, OpenAIResponsesCompat, AnthropicMessagesCompat]
    ] = None

    @model_validator(mode="before")
    @classmethod
    def _resolve_compat_class(cls, data):
        """按 api 显式判别 compat 的 union 成员，不依赖 smart-union 猜测。

        三个 compat 类的字段全部 Optional 且部分同名，裸 dict 走 smart-union
        可能判错成员（如 ``{"supports_developer_role": true}`` 同时命中两类）。
        判别依据与 ``_set_default_compat`` 一致：以 api 为准。
        未知 api 的 compat 保持原样，交给 pydantic 默认处理。
        """
        if isinstance(data, dict):
            compat = data.get("compat")
            if isinstance(compat, dict):
                api = data.get("api")
                api_value = getattr(api, "value", api)
                compat_cls = _COMPAT_CLASS_BY_API.get(api_value)
                if compat_cls is not None:
                    data = dict(data)
                    data["compat"] = compat_cls.model_validate(compat)
        return data

    @model_validator(mode="after")
    def _set_default_compat(self):
        """根据API类型设置兼容性配置"""
        if self.compat is None:
            if self.api == KnownApi.OPENAI_COMPLETIONS:
                self.compat = OpenAICompletionsCompat()
            elif self.api == KnownApi.OPENAI_RESPONSES:
                self.compat = OpenAIResponsesCompat()
            elif self.api == KnownApi.ANTHROPIC_MESSAGES:
                self.compat = AnthropicMessagesCompat()
        return self


class Cost(NovaBaseModel):
    """成本明细"""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


class Usage(NovaBaseModel):
    """令牌使用统计"""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    # Anthropic 1h 缓存写入量（cache_write 的子集）
    cache_write_1h: Optional[int] = None
    # reasoning/thinking tokens（output 的子集，provider 报告时填充）
    reasoning: Optional[int] = None
    total_tokens: int = 0
    cost: Cost = Field(default_factory=Cost)
