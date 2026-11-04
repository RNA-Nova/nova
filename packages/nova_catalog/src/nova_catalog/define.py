"""管线的词汇层（对位 pi ``catalog/define.ts`` 的 ``ProviderDefinition``）。

每家 provider 的知识是一个 ``ProviderDefinition``——**声明式数据为主**
（区别于 pi 的全函数定义），并带三个**规模逃生口**（43 家扩员的类型化
出口，pi 的钩子槽对位）：

- ``derive_from``：跨 provider 派生（azure←openai 型）——引擎按它建
  依赖序，先建源家成品、再套本家盖戳与修正；
- ``finalize``：per-model 逃生口（条件旗标 / 白名单外字段——如
  promptCache、按模型分档的输入限制），跑在修正之后；
- ``finalize_all``：跨模型逃生口（fallback 链、生命周期筛选——如火山
  ark 在售过滤），跑在整表定稿前。

95% 的 provider 只用数据字段；逃生口按内容命名、类型化签名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from nova_protocol import KnownApi, ModelFields, ModelFields


@dataclass(frozen=True)
class ProviderDefinition:
    """一家 provider 的构建期知识全集（对位 pi 的 ``ProviderDefinition``）。

    语义（与运行期 finalize 引擎逐字一致）：

    - ``api``：目录协议戳（``KnownApi``——正典枚举，非裸字符串）；
    - ``compat_defaults``：垫在字段包自带 compat **之下**（模型值赢）；
    - ``model_corrections``：glob → 修正包；``compat``/``cost``/
      ``thinking_level_map`` 按键合并（修正键覆盖源键），
      ``context_window``/``max_tokens`` 为硬修正（覆盖优先）；
    - ``pinned_models``：数据源缺失但端点实测在售的整模型（先合并、
      源同 id 不覆盖）；
    - ``models_dev_key``：models.dev 键与 provider id 不同名时的别名
      （如 kimi-coding → kimi-code-plan-global）；
    - 逃生口见模块 docstring（``derive_from``/``finalize``/``finalize_all``）。
    """

    id: str
    name: str
    base_url: str
    api: KnownApi
    models_dev_key: Optional[str] = None
    compat_defaults: Mapping[str, Any] = field(default_factory=dict)
    model_corrections: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    pinned_models: Mapping[str, ModelFields] = field(default_factory=dict)
    derive_from: Optional[str] = None
    finalize: Optional[Callable[[str, ModelFields, Any], None]] = None
    """per-model 逃生口：``(model_id, fields, ctx) -> None``（原地改 fields）。"""
    finalize_all: Optional[Callable[[List[ModelFields], Any], None]] = None
    """跨模型逃生口：``(models, ctx) -> None``（原地改列表——过滤/合并/链接）。"""


def define_provider(**kwargs: Any) -> ProviderDefinition:
    """登记一家 provider 定义（对位 pi ``defineProvider``——构造壳）。"""
    return ProviderDefinition(**kwargs)


__all__ = ["ProviderDefinition", "define_provider"]
