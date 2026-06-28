"""
基础模型类型定义
包含模型定义、模型成本、用量统计
"""

from typing import Optional, Dict, List, Literal, Union
from pydantic import Field, model_validator
from .base_model import NovaBaseModel

from .enums import Api, Provider, KnownApi, ThinkingLevelMap
from .compat import OpenAICompletionsCompat, OpenAIResponsesCompat


class ModelCost(NovaBaseModel):
    """模型成本（$/百万tokens）"""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


class Model(NovaBaseModel):
    """模型定义"""

    id: str
    name: str
    api: Api
    provider: Provider
    base_url: str
    reasoning: bool
    thinking_level_map: Optional[ThinkingLevelMap] = None
    input_types: List[Literal["text", "image"]]
    cost: ModelCost
    context_window: int
    max_tokens: int
    headers: Optional[Dict[str, str]] = None
    compat: Optional[Union[OpenAICompletionsCompat, OpenAIResponsesCompat]] = None

    @model_validator(mode="after")
    def _set_default_compat(self):
        """根据API类型设置兼容性配置"""
        if self.compat is None:
            if self.api == KnownApi.OPENAI_COMPLETIONS:
                self.compat = OpenAICompletionsCompat()
            elif self.api == KnownApi.OPENAI_RESPONSES:
                self.compat = OpenAIResponsesCompat()
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
    total_tokens: int = 0
    cost: Cost = Field(default_factory=Cost)
