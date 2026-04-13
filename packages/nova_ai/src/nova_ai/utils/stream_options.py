"""
流选项工具函数
处理模型请求的选项配置
"""

from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass, field,InitVar

from ..core.enums import ThinkingLevel, CacheRetention, Transport
from ..models import Model
from mashumaro.mixins.json import DataClassJSONMixin
from mashumaro.config import BaseConfig

@dataclass
class ThinkingBudgets(DataClassJSONMixin):
    """各思考级别的token预算"""
    minimal: Optional[int] = None
    low: Optional[int] = None
    medium: Optional[int] = None
    high: Optional[int] = None


from mashumaro.mixins.json import DataClassJSONMixin
from mashumaro.config import BaseConfig
from dataclasses import dataclass, field, InitVar
from typing import Optional, Dict, Any, Callable

@dataclass
class StreamOptions(DataClassJSONMixin):
    """流式选项"""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    api_key: Optional[str] = None
    transport: Optional[Any] = None
    cache_retention: Optional[Any] = None
    session_id: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    max_retry_delay_ms: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    
    # 使用 InitVar 标记为初始化变量，不会被序列化
    signal: InitVar[Optional[Any]] = None
    on_payload: InitVar[Optional[Callable]] = None
    
    def __post_init__(self, signal: Optional[Any] = None, on_payload: Optional[Callable] = None):
        """存储 InitVar 变量到实例属性"""
        self.signal = signal
        self.on_payload = on_payload


@dataclass
class SimpleStreamOptions(StreamOptions):
    """简单流式选项（带推理配置）"""
    reasoning: Optional[ThinkingLevel] = None
    thinking_budgets: Optional[ThinkingBudgets] = None


def build_base_options(
    model: Model,
    options: Optional[SimpleStreamOptions] = None,
    api_key: Optional[str] = None
) -> StreamOptions:
    """
    构建基础流式选项
    
    Args:
        model: 模型对象
        options: 简单流式选项
        api_key: API密钥
        
    Returns:
        流式选项对象
    """
    return StreamOptions(
        temperature=options.temperature if options else None,
        max_tokens=options.max_tokens or min(model.max_tokens, 32000) if options else min(model.max_tokens, 32000),
        signal=options.signal if options else None,
        api_key=api_key or (options.api_key if options else None),
        transport=options.transport if options else None,
        cache_retention=options.cache_retention if options else None,
        session_id=options.session_id if options else None,
        headers=options.headers if options else None,
        on_payload=options.on_payload if options else None,
        max_retry_delay_ms=options.max_retry_delay_ms if options else None,
        metadata=options.metadata if options else None,
    )


def clamp_reasoning(effort: Optional[ThinkingLevel]) -> Optional[ThinkingLevel]:
    """
    将xhigh思考级别降级为high
    
    Args:
        effort: 思考级别
        
    Returns:
        降级后的思考级别，如果输入为None则返回None
    """
    if effort is None:
        return None
    return ThinkingLevel.HIGH if effort == ThinkingLevel.XHIGH else effort


def adjust_max_tokens_for_thinking(
    base_max_tokens: int,
    model_max_tokens: int,
    reasoning_level: ThinkingLevel,
    custom_budgets: Optional[ThinkingBudgets] = None
) -> Dict[str, int]:
    """
    为思考过程调整最大token数
    
    Args:
        base_max_tokens: 基础最大token数
        model_max_tokens: 模型最大token数
        reasoning_level: 思考级别
        custom_budgets: 自定义预算
        
    Returns:
        包含调整后的max_tokens和thinking_budget的字典
        
    Example:
        >>> result = adjust_max_tokens_for_thinking(4096, 8192, ThinkingLevel.MEDIUM)
        >>> print(result['max_tokens'], result['thinking_budget'])
    """
    # 默认预算
    default_budgets = ThinkingBudgets(
        minimal=1024,
        low=2048,
        medium=8192,
        high=16384
    )
    
    # 合并自定义预算
    budgets = ThinkingBudgets(
        minimal=custom_budgets.minimal if custom_budgets and custom_budgets.minimal is not None else default_budgets.minimal,
        low=custom_budgets.low if custom_budgets and custom_budgets.low is not None else default_budgets.low,
        medium=custom_budgets.medium if custom_budgets and custom_budgets.medium is not None else default_budgets.medium,
        high=custom_budgets.high if custom_budgets and custom_budgets.high is not None else default_budgets.high,
    )
    
    min_output_tokens = 1024
    level = clamp_reasoning(reasoning_level)
    
    if level is None:
        raise ValueError(f"Invalid reasoning level: {reasoning_level}")
    
    # 根据级别获取预算
    thinking_budget = getattr(budgets, level.value)
    if thinking_budget is None:
        raise ValueError(f"No budget defined for thinking level: {level}")
    
    max_tokens = min(base_max_tokens + thinking_budget, model_max_tokens)
    
    # 确保有足够的输出token
    if max_tokens <= thinking_budget:
        thinking_budget = max(0, max_tokens - min_output_tokens)
    
    return {
        'max_tokens': max_tokens,
        'thinking_budget': thinking_budget
    }


def build_thinking_params(
    reasoning_level: Optional[ThinkingLevel],
    custom_budgets: Optional[ThinkingBudgets] = None
) -> Optional[Dict[str, Any]]:
    """
    构建思考参数（用于不同提供商的API）
    
    Args:
        reasoning_level: 思考级别
        custom_budgets: 自定义预算
        
    Returns:
        思考参数字典，如果不需要思考则返回None
    """
    if reasoning_level is None:
        return None
    
    level = clamp_reasoning(reasoning_level)
    
    # 根据不同提供商格式返回
    return {
        'type': 'reasoning',
        'effort': level.value if level else None,
        'budget_tokens': get_thinking_budget(level, custom_budgets)
    }


def get_thinking_budget(
    level: Optional[ThinkingLevel],
    custom_budgets: Optional[ThinkingBudgets] = None
) -> Optional[int]:
    """
    获取指定思考级别的token预算
    
    Args:
        level: 思考级别
        custom_budgets: 自定义预算
        
    Returns:
        token预算，如果级别无效则返回None
    """
    if level is None:
        return None
    
    default_budgets = {
        ThinkingLevel.MINIMAL: 1024,
        ThinkingLevel.LOW: 2048,
        ThinkingLevel.MEDIUM: 8192,
        ThinkingLevel.HIGH: 16384,
        ThinkingLevel.XHIGH: 32768,
    }
    
    # 如果有自定义预算，优先使用
    if custom_budgets:
        budget = getattr(custom_budgets, level.value, None)
        if budget is not None:
            return budget
    
    return default_budgets.get(level)