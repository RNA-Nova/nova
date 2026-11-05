"""构建流水线编排（对位 pi ``catalog/build.ts``）。

职责只剩编排：**取数 → 翻译 → 修正 → 盖戳 → 逃生口（finalize/finalize_all）**，
外加跨 provider 派生（``derive_from``，对位 pi ``ctx.providerModels``）。

- 拉取归 ``sources/``（models.dev / ark）；
- 翻译归 ``sources/models_dev.py``（``models_dev_to_nova``）；
- 修正语义归 ``engine.py``（``apply_corrections``）；
- 知识归 ``providers/`` 的 ``ProviderDefinition``；
- 上下文归 ``context.py``（``BuildContext``——源捆/派生/环检测）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from nova_protocol import ModelFields

from .context import BuildContext
from .define import ProviderDefinition
from .engine import stamp_fields
from .sources.models_dev import ModelsDevSource, models_dev_to_nova


def _stamp(
    provider: ProviderDefinition, model_id: str, fields: ModelFields
) -> ModelFields:
    """盖戳（修正后统一）——实现归 ``engine.stamp_fields``（单一实现点）。"""
    return stamp_fields(
        provider,
        model_id,
        fields,
        provider_id=provider.id,
        base_url=provider.base_url,
    )


def build_provider_models(
    provider: ProviderDefinition,
    raw_models: Dict[str, Dict[str, Any]],
    *,
    strict: bool = False,
    static: bool = False,
) -> Dict[str, ModelFields]:
    """原始条目 → 修正 → 盖戳 → 最终 ``Model`` 字段（单家主流水线）。

    ``static=True``：条目已是最终字段形态（跳过翻译）。
    """
    models: Dict[str, ModelFields] = {}

    for model_id, fields in provider.pinned_models.items():
        models[model_id] = _stamp(provider, model_id, fields)

    for model_id, entry in raw_models.items():
        if model_id in models:
            continue
        fields = (
            ModelFields(**entry)
            if static
            else models_dev_to_nova(provider.id, entry, strict)
        )
        if fields is None:
            continue
        models[model_id] = _stamp(provider, model_id, fields)
    return models


def _derive_models(
    provider: ProviderDefinition, ctx: BuildContext
) -> Dict[str, ModelFields]:
    """跨 provider 派生（``derive_from``）：取源家成品 → 换本家盖戳与修正。

    源家的 api/base_url/provider 一律换成本家（派生即重托管）；本家的
    compat 默认与模型级修正照常应用（逃生口亦照常）。
    """
    base_models = ctx.provider_models(provider.derive_from or "")
    models: Dict[str, ModelFields] = {}
    for fields in base_models:
        model_id = str(fields.get("id"))
        stamped = _stamp(provider, model_id, fields)
        # 派生即重托管：api/base_url/provider 强制本家（压过源家盖戳）
        stamped["api"] = provider.api.value
        stamped["base_url"] = provider.base_url
        stamped["provider"] = provider.id
        models[model_id] = stamped
    return models


def build_one(provider: ProviderDefinition, ctx: BuildContext) -> List[ModelFields]:
    """单家构建（派生序/直取 + 主流水线 + 逃生口钩子）。

    相位（对位 pi）：取数/派生 → 修正 → 盖戳 → ``finalize``（per-model）
    → ``finalize_all``（跨模型）。
    """
    if provider.derive_from:
        built = _derive_models(provider, ctx)
    else:
        raw = ctx.raw_for(provider)
        built = build_provider_models(provider, raw, strict=ctx.strict)
    models = list(built.values())

    if provider.finalize is not None:
        for fields in models:
            provider.finalize(str(fields.get("id")), fields, ctx)
    if provider.finalize_all is not None:
        provider.finalize_all(models, ctx)
    return models


async def prepare_sources(
    providers: List[ProviderDefinition], ctx: BuildContext
) -> None:
    """预拉全部直取家的源切片（async 段收口——派生家无需拉取）。"""
    for provider in providers:
        if provider.derive_from:
            continue
        key = provider.models_dev_key or provider.id
        ctx.set_raw(
            provider.id,
            ((await ctx.source.payload()).get(key) or {}).get("models") or {},
        )


__all__ = ["build_one", "build_provider_models", "prepare_sources"]
