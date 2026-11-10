"""模型目录统一构建编排（对位 pi ``catalog/build.ts``）。

一次 ``build_catalog()`` 调用内部持有全部构建状态：源切片、stage-one、派生
递归和 models.dev 来源表。外部没有“先注册构建器”之类的时序契约。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from nova_protocol import ModelFields

from .define import ProviderContext, ProviderDefinition
from .engine import apply_compat_defaults, stamp_fields
from .families.anthropic import apply_claude_family_rules
from .input_limits import apply_image_resize_defaults
from .reasoning import apply_models_dev_effort
from .sources.models_dev import ModelsDevSource, models_dev_to_nova

_SourceKey = Tuple[str, str]
_SourceEntry = Dict[str, Any]


def _build_provider_models(
    provider: ProviderDefinition,
    raw_models: Dict[str, Dict[str, Any]],
    *,
    strict: bool = False,
    include_compat_defaults: bool = True,
) -> Dict[str, ModelFields]:
    """把 pinned + models.dev 原始条目修正并盖戳为模型字段包。"""
    models: Dict[str, ModelFields] = {}
    for model_id, fields in provider.pinned_models.items():
        models[model_id] = stamp_fields(
            provider,
            model_id,
            fields,
            provider_id=provider.id,
            base_url=provider.base_url,
            include_compat_defaults=include_compat_defaults,
        )
    for model_id, entry in raw_models.items():
        if model_id in models:
            continue
        fields = models_dev_to_nova(
            provider.id, entry, strict, skip_deprecated=provider.skip_deprecated
        )
        if fields is None:
            continue
        models[model_id] = stamp_fields(
            provider,
            model_id,
            fields,
            provider_id=provider.id,
            base_url=provider.base_url,
            include_compat_defaults=include_compat_defaults,
        )
    return models


def _source_reasoning(entry: Optional[_SourceEntry]) -> Optional[Dict[str, Any]]:
    if entry is None:
        return None
    capabilities = entry.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    reasoning = capabilities.get("reasoning")
    return reasoning if isinstance(reasoning, dict) else None


async def build_catalog(
    providers: Sequence[ProviderDefinition],
    source: ModelsDevSource,
    *,
    strict: bool = False,
) -> Dict[str, List[ModelFields]]:
    """完成“取数 → stage-one/派生 → 共享规则 → finalize”的完整构建。"""
    try:
        payload = await source.payload()
    except Exception:
        if strict:
            raise
        payload = {}

    definitions = {provider.id: provider for provider in providers}
    if len(definitions) != len(providers):
        raise ValueError("Duplicate provider definitions")
    raw_by_provider: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for provider in providers:
        if provider.derive_from:
            continue
        key = provider.models_dev_key or provider.id
        provider_slice = payload.get(key)
        models = (
            provider_slice.get("models") if isinstance(provider_slice, dict) else None
        )
        if not isinstance(models, dict):
            if strict:
                raise ValueError(
                    f"models.dev source is missing a valid models slice for {key}"
                )
            models = {}
        raw_by_provider[provider.id] = models

    stage_one: Dict[str, List[ModelFields]] = {}
    building: set[str] = set()
    sources: Dict[_SourceKey, _SourceEntry] = {}

    def _context(provider_id: str) -> ProviderContext:
        def _source(model_id: str) -> Optional[_SourceEntry]:
            entry = sources.get((provider_id, model_id))
            return copy.deepcopy(entry) if entry is not None else None

        return ProviderContext(
            provider_id=provider_id,
            strict=strict,
            provider_models=lambda id: copy.deepcopy(_build_stage_one(id)),
            models_dev_source=_source,
        )

    def _derive(provider: ProviderDefinition) -> List[ModelFields]:
        parent_id = provider.derive_from
        assert parent_id is not None
        derived: List[ModelFields] = []
        for fields in _build_stage_one(parent_id):
            model_id = fields.get("id")
            if not isinstance(model_id, str) or not model_id:
                raise ValueError(f"{parent_id}: derived model is missing id")
            source_entry = sources.get((parent_id, model_id))
            stamped = stamp_fields(
                provider,
                model_id,
                copy.deepcopy(fields),
                provider_id=provider.id,
                base_url=provider.base_url,
                include_compat_defaults=False,
            )
            stamped["api"] = provider.api.value
            stamped["base_url"] = provider.base_url
            stamped["provider"] = provider.id
            if source_entry is not None:
                sources[(provider.id, model_id)] = source_entry
                if provider.map_model is not None:
                    provider.map_model(model_id, stamped, source_entry)
            derived.append(stamped)
        return derived

    def _build_stage_one(provider_id: str) -> List[ModelFields]:
        cached = stage_one.get(provider_id)
        if cached is not None:
            return cached
        provider = definitions.get(provider_id)
        if provider is None:
            raise ValueError(f"Unknown provider for derive: {provider_id}")
        if provider_id in building:
            raise ValueError(f"Provider {provider_id} depends on itself (derive cycle)")
        building.add(provider_id)
        try:
            if provider.derive_from:
                built = _derive(provider)
            else:
                raw = raw_by_provider[provider.id]
                built = list(
                    _build_provider_models(
                        provider, raw, strict=strict, include_compat_defaults=False
                    ).values()
                )
                for fields in built:
                    model_id = fields.get("id")
                    if isinstance(model_id, str) and model_id in raw:
                        source_entry = raw[model_id]
                        sources[(provider.id, model_id)] = source_entry
                        if provider.map_model is not None:
                            provider.map_model(model_id, fields, source_entry)
            stage_one[provider_id] = built
            return built
        finally:
            building.discard(provider_id)

    # 派生只允许读取 stage-one；先把所有依赖构建完，再运行共享规则和 hook。
    for provider in providers:
        _build_stage_one(provider.id)

    catalog: Dict[str, List[ModelFields]] = {}
    for provider in providers:
        # 独立列表：finalize_all 的增删排序不得回写 stage-one 缓存。
        models = list(stage_one[provider.id])
        context = _context(provider.id)
        for fields in models:
            apply_compat_defaults(provider, fields)
            # native effort 门控可能依赖 adaptive 标志，族规则必须先到位。
            # 这是 pi 同序缺陷的主动修复，不是机械照搬其调用顺序。
            apply_claude_family_rules(fields)
            apply_models_dev_effort(
                fields,
                _source_reasoning(sources.get((provider.id, fields.get("id", "")))),
            )
        if provider.finalize is not None:
            for fields in models:
                model_id = fields.get("id")
                if not isinstance(model_id, str) or not model_id:
                    raise ValueError(f"{provider.id}: generated model is missing id")
                provider.finalize(model_id, fields, context)
        for fields in models:
            apply_image_resize_defaults(fields)
        if provider.finalize_all is not None:
            provider.finalize_all(models, context)
        ids = [fields.get("id") for fields in models]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{provider.id}: duplicate model ids after finalize")
        catalog[provider.id] = models
    return catalog


__all__ = ["build_catalog"]
