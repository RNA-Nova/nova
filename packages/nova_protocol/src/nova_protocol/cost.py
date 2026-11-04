"""模型成本词汇（叶子模块——``model.py`` 与 ``compat.py`` 共同的下游）。

拆分原因：``compat.py`` 的 ``allowed_fallback_models`` 条目携带本地定价
（fallback 应答按 fallback 费率计成本），而 ``model.py`` 引用 compat
类——cost 住 model.py 即成环。归叶子后两侧同引，单一正典。
"""

from typing import List, Optional

from .base_model import NovaBaseModel


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


__all__ = ["ModelCost", "ModelCostRates", "ModelCostTier"]
