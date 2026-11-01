"""构建期上下文（对位 pi ``ProviderContext``——43 家扩员的接缝）。

管线基建与 provider 定义之间的唯一注入对象：

- ``source``：主目录源（``ModelsDevSource``——构建期 60s 共享缓存）；
- ``ark_status``：火山方舟在售状态表（账户级生命周期数据——构建期
  由入口拉取一次注入；volcengine 的 ``finalize_all`` 消费）；
- ``strict``：数据源异常即抛（``--strict`` 闸）；
- ``provider_models()``：跨 provider 派生（``derive_from`` 的执行点——
  依赖序构建 + 环检测 + 成品克隆，对位 pi ``ctx.providerModels()``）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from nova_protocol import ModelFields

from .define import ProviderDefinition
from .sources.models_dev import ModelsDevSource


class BuildContext:
    """构建期上下文（依赖序构建机 + 源捆 + 派生执行点）。"""

    def __init__(
        self,
        providers: Dict[str, ProviderDefinition],
        source: ModelsDevSource,
        *,
        strict: bool = False,
        ark_status: Optional[Dict[str, str]] = None,
    ) -> None:
        self._providers = providers
        self.source = source
        self.strict = strict
        self.ark_status = ark_status
        self._built: Dict[str, List[ModelFields]] = {}
        self._building: Set[str] = set()
        self._raw: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # 编排器回填（build_provider 完成后登记，供派生方取）
        self._build_fn: Optional[Any] = None

    def set_raw(self, provider_id: str, raw: Dict[str, Dict[str, Any]]) -> None:
        """登记某家的源切片（``prepare_sources`` 预拉注入）。"""
        self._raw[provider_id] = raw

    def raw_for(self, provider: ProviderDefinition) -> Dict[str, Dict[str, Any]]:
        """取某家的源切片（未预拉返回空——如派生家）。"""
        return self._raw.get(provider.id, {})

    def register_build_fn(self, build_fn: Any) -> None:
        """编排器注册单家构建函数（派生回调与主流水线同一实现）。"""
        self._build_fn = build_fn

    def provider_models(self, provider_id: str) -> List[ModelFields]:
        """取另一家 provider 的建成品（跨 provider 派生）。

        依赖序：未建先建（递归），环即报错（pi 同款环检测）。
        """
        built = self._built.get(provider_id)
        if built is not None:
            return [ModelFields(**m) for m in built]  # 克隆防污染
        definition = self._providers.get(provider_id)
        if definition is None:
            raise ValueError(f"Unknown provider for derive: {provider_id}")
        if provider_id in self._building:
            raise ValueError(f"Provider {provider_id} depends on itself (derive cycle)")
        if self._build_fn is None:
            raise RuntimeError("BuildContext: build_fn 未注册")
        self._building.add(provider_id)
        try:
            assert self._build_fn is not None
            built = self._build_fn(definition)
        finally:
            self._building.discard(provider_id)
        self._built[provider_id] = built
        return [ModelFields(**m) for m in built]

    def register_built(self, provider_id: str, models: List[ModelFields]) -> None:
        """编排器登记建成品（供后续派生）。"""
        self._built[provider_id] = models


__all__ = ["BuildContext"]
