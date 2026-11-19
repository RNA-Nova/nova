"""模型目录统一构建编排（对位 pi ``catalog/build.ts``）。

provider 的 models/images 钩子负责目录内容；本模块只执行去重、静态 compat
默认、族规则、effort、finalize 和图像输入限制。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from nova_protocol import ModelFields

from .define import ProviderContext, ProviderDefinition
from .families.anthropic import apply_claude_family_rules
from .input_limits import apply_image_resize_defaults
from .reasoning import apply_models_dev_effort
from .sources.load import CatalogSources

_SourceKey = tuple[str, str]
_SourceEntry = Dict[str, Any]


@dataclass
class ProviderCatalog:
    """一家 provider 的分操作目录；各操作使用独立列表。"""

    chat: List[ModelFields]
    images: List[ModelFields]
    classifiers: List[ModelFields]


def build_catalog(
    providers: List[ProviderDefinition] | tuple[ProviderDefinition, ...],
    source_data: CatalogSources,
    *,
    strict: bool = False,
) -> Dict[str, ProviderCatalog]:
    """调用各家构建钩子，并执行共享定稿规则。"""
    ids = [provider.id for provider in providers]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate provider definitions")

    sources: Dict[_SourceKey, _SourceEntry] = {}

    def _context(provider_id: str) -> ProviderContext:
        def _source(model: ModelFields) -> Optional[_SourceEntry]:
            provider = model.get("provider")
            model_id = model.get("id")
            if not isinstance(provider, str) or not isinstance(model_id, str):
                return None
            entry = sources.get((provider, model_id))
            return copy.deepcopy(entry) if entry is not None else None

        def _record(model: ModelFields, source: _SourceEntry) -> None:
            provider = model.get("provider")
            model_id = model.get("id")
            if (
                not isinstance(provider, str)
                or not isinstance(model_id, str)
                or not model_id
            ):
                raise ValueError(
                    "Cannot record models.dev source without model identity"
                )
            sources[(provider, model_id)] = copy.deepcopy(source)

        return ProviderContext(
            provider_id=provider_id,
            sources=source_data,
            strict=strict,
            models_dev_source=_source,
            record_models_dev_source=_record,
        )

    catalog: Dict[str, ProviderCatalog] = {}
    for provider in providers:
        context = _context(provider.id)
        built = provider.models(context) if provider.models else []
        for fields in built:
            if fields.get("provider") != provider.id:
                raise ValueError(
                    f"{provider.id} produced {fields.get('provider')}/"
                    f"{fields.get('id')} for another provider"
                )

        models: List[ModelFields] = []
        seen: set[str] = set()
        for fields in built:
            model_id = fields.get("id")
            if not isinstance(model_id, str) or not model_id or model_id in seen:
                continue
            seen.add(model_id)
            models.append(fields)

        for fields in models:
            api = fields.get("api")
            api_value = getattr(api, "value", api)
            if not isinstance(api_value, str):
                raise ValueError(f"{provider.id}: generated model is missing api")
            default = provider.compat.get(api_value)
            if default:
                fields["compat"] = {**default, **(fields.get("compat") or {})}
            # native effort 门控依赖 adaptive 标志，族规则先到位（实测修正）。
            apply_claude_family_rules(fields)
            source = context.models_dev_source(fields)
            capabilities = source.get("capabilities") if source is not None else None
            reasoning = (
                capabilities.get("reasoning")
                if isinstance(capabilities, dict)
                else None
            )
            apply_models_dev_effort(
                fields, reasoning if isinstance(reasoning, dict) else None
            )
        if provider.finalize is not None:
            for fields in models:
                provider.finalize(fields, context)
        for fields in models:
            apply_image_resize_defaults(fields)
            fields["type"] = "chat"
        if provider.finalize_all is not None:
            provider.finalize_all(models, context)
        model_ids = [fields.get("id") for fields in models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError(f"{provider.id}: duplicate model ids after finalize")

        images = provider.images(context) if provider.images else []
        for fields in images:
            apply_image_resize_defaults(fields)
            fields["type"] = "image"
        classifiers = provider.classifiers(context) if provider.classifiers else []
        for fields in classifiers:
            fields["type"] = "classifier"
        catalog[provider.id] = ProviderCatalog(
            chat=models, images=images, classifiers=classifiers
        )
    return catalog


__all__ = ["ProviderCatalog", "build_catalog"]
