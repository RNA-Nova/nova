"""
使用统计类型定义
"""

from dataclasses import dataclass, field

from mashumaro.mixins.json import DataClassJSONMixin


@dataclass
class Cost(DataClassJSONMixin):
    """成本明细"""
    input: float = 0.0          # 输入成本
    output: float = 0.0         # 输出成本
    cache_read: float = 0.0     # 缓存读取成本
    cache_write: float = 0.0    # 缓存写入成本
    total: float = 0.0          # 总成本


@dataclass
class Usage(DataClassJSONMixin):
    """令牌使用统计"""
    input: int = 0               # 输入令牌数
    output: int = 0              # 输出令牌数
    cache_read: int = 0          # 缓存读取令牌数
    cache_write: int = 0         # 缓存写入令牌数
    total_tokens: int = 0        # 总令牌数
    cost: Cost = field(default_factory=Cost)  # 成本明细