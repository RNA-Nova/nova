"""
基础模型类型定义
"""

from typing import Optional, Dict, List, Literal, Union, TypeVar, Generic
from dataclasses import dataclass, field
from enum import Enum

from mashumaro.mixins.json import DataClassJSONMixin

from ..core.usage import Usage,Cost

from ..core.enums import Api, Provider, KnownApi, KnownProvider
from ..compat.openai import OpenAICompletionsCompat, OpenAIResponsesCompat


@dataclass
class ModelCost(DataClassJSONMixin):
    """模型成本（$/百万tokens）"""
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass
class Model(DataClassJSONMixin):
    """模型定义"""
    id: str
    name: str
    api: Api
    provider: Provider
    base_url: str
    reasoning: bool
    input_types: List[Literal["text", "image"]]
    cost: ModelCost
    context_window: int
    max_tokens: int
    headers: Optional[Dict[str, str]] = None
    compat: Optional[Union[OpenAICompletionsCompat, OpenAIResponsesCompat]] = None
    
    def __post_init__(self):
        """根据API类型设置兼容性配置"""
        if self.compat is None:
            if self.api == KnownApi.OPENAI_COMPLETIONS:
                self.compat = OpenAICompletionsCompat()
            elif self.api == KnownApi.OPENAI_RESPONSES:
                self.compat = OpenAIResponsesCompat()


# 类型别名
TApi = TypeVar('TApi', bound=Api)
ModelType = Model  # 在Python中，我们可以使用类型检查

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
        usage.cost.input + 
        usage.cost.output + 
        usage.cost.cache_read + 
        usage.cost.cache_write
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

def models_are_equal(
    a: Optional[Model],
    b: Optional[Model]
) -> bool:
    """
    检查两个模型是否相等（比较 id 和 provider）
    
    如果任一模型为 None，返回 False
    
    Args:
        a: 第一个模型
        b: 第二个模型
        
    Returns:
        两个模型是否相等
    """
    if a is None or b is None:
        return False
    return a.id == b.id and a.provider == b.provider