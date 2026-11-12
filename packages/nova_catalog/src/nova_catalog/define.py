"""模型目录构建词汇与 models.dev 辅助函数（对位 pi ``catalog/define.ts``）。

provider 通过 ``models`` / ``images`` 钩子完整构造目录；公共构建器只负责
共享阶段。本模块不持有派生、通用修正器或其他中央表单机制。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from nova_protocol import KnownApi, ModelFields

from .sources.load import CatalogSources
from .sources.models_dev import models_dev_to_nova

MapModelFn = Callable[[ModelFields, Dict[str, Any]], ModelFields]
FinalizeModelFn = Callable[[ModelFields, "ProviderContext"], None]
FinalizeAllFn = Callable[[List[ModelFields], "ProviderContext"], None]
ApiCompatDefaults = Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class ProviderContext:
    """provider hook 的构建能力（行为容器，故用 dataclass）。"""

    provider_id: str
    sources: CatalogSources
    strict: bool
    models_dev_source: Callable[[ModelFields], Optional[Dict[str, Any]]]
    record_models_dev_source: Callable[[ModelFields, Dict[str, Any]], None]


@dataclass(frozen=True)
class ProviderDefinition:
    """一家 provider 的构建定义（对位 pi ProviderDefinition）。"""

    id: str
    models: Optional[Callable[[ProviderContext], List[ModelFields]]] = None
    images: Optional[Callable[[ProviderContext], List[ModelFields]]] = None
    classifiers: Optional[Callable[[ProviderContext], List[ModelFields]]] = None
    compat: ApiCompatDefaults = field(default_factory=dict)
    finalize: Optional[FinalizeModelFn] = None
    finalize_all: Optional[FinalizeAllFn] = None


def define_provider(
    *,
    id: str,
    models: Optional[Callable[[ProviderContext], List[ModelFields]]] = None,
    images: Optional[Callable[[ProviderContext], List[ModelFields]]] = None,
    classifiers: Optional[Callable[[ProviderContext], List[ModelFields]]] = None,
    compat: Optional[ApiCompatDefaults] = None,
    finalize: Optional[FinalizeModelFn] = None,
    finalize_all: Optional[FinalizeAllFn] = None,
) -> ProviderDefinition:
    """构造 provider 定义；models/images/classifiers 分操作构建。"""
    return ProviderDefinition(
        id=id,
        models=models,
        images=images,
        classifiers=classifiers,
        compat=compat or {},
        finalize=finalize,
        finalize_all=finalize_all,
    )


def stamp_fields(
    model_id: str,
    fields: ModelFields,
    *,
    provider_id: str,
    base_url: Optional[str],
    api: Optional[str] = None,
) -> ModelFields:
    """补齐身份：源 API 优先；base_url 非 None 时按 provider 端点覆盖。"""
    stamped = ModelFields(**fields)
    resolved_api = stamped.get("api") or api
    if not isinstance(resolved_api, str) or not resolved_api:
        raise ValueError(
            f"Cannot determine catalog API for {provider_id}/{model_id}: "
            "the source has no api and the provider definition declares none"
        )
    stamped["id"] = model_id
    if base_url is not None:
        stamped["base_url"] = base_url
    stamped["api"] = resolved_api
    stamped["provider"] = provider_id
    return stamped


def _api_value(api: str | KnownApi) -> str:
    return api.value if isinstance(api, KnownApi) else api


def validate_pinned_model_type(
    provider_id: str, model_id: str, fields: ModelFields
) -> str:
    """验收作者侧 pinned 类型；仅键缺席按 chat 解释。"""
    model_type = fields["type"] if "type" in fields else "chat"
    if model_type not in ("chat", "image"):
        raise ValueError(
            f"{provider_id}: unknown pinned model type {model_type!r} for {model_id}"
        )
    return model_type


def models_dev_models(
    ctx: ProviderContext,
    source_provider: str,
    *,
    api: str | KnownApi,
    base_url: str,
    skip_deprecated: bool = False,
    map_model: Optional[MapModelFn] = None,
    pinned_models: Optional[Mapping[str, ModelFields]] = None,
    record_reasoning: bool = True,
) -> List[ModelFields]:
    """用 models.dev 构建聊天目录；pinned 优先，map 可改写模型。"""
    models: List[ModelFields] = []
    seen: set[str] = set()
    for model_id, fields in (pinned_models or {}).items():
        if validate_pinned_model_type(ctx.provider_id, model_id, fields) != "chat":
            continue
        models.append(
            stamp_fields(
                model_id,
                copy.deepcopy(fields),
                provider_id=ctx.provider_id,
                base_url=base_url,
                api=_api_value(api),
            )
        )
        seen.add(model_id)

    provider_slice = ctx.sources.models_dev.get(source_provider)
    source_models = (
        provider_slice.get("models") if isinstance(provider_slice, dict) else None
    )
    if not isinstance(source_models, dict):
        if ctx.strict:
            raise ValueError(
                f"models.dev source is missing a valid models slice for {source_provider}"
            )
        source_models = {}

    for _model_key, entry in source_models.items():
        fields = models_dev_to_nova(
            ctx.provider_id, entry, ctx.strict, skip_deprecated=skip_deprecated
        )
        if fields is None:
            continue
        # pi 以条目内已校验的 source.id 为准；外层字典 key 只是索引，
        # 不得在键值漂移时冒充发往厂商的模型 ID。
        source_id = entry.get("id")
        if not isinstance(source_id, str) or not source_id or source_id in seen:
            continue
        fields["id"] = source_id
        stamped = stamp_fields(
            source_id,
            fields,
            provider_id=ctx.provider_id,
            base_url=base_url,
            api=_api_value(api),
        )
        mapped = map_model(stamped, copy.deepcopy(entry)) if map_model else stamped
        final_id = mapped.get("id")
        if not isinstance(final_id, str) or not final_id:
            raise ValueError(f"{ctx.provider_id}: mapped model is missing id")
        if final_id in seen:
            # 与 build 的去重一致：第一个条目连同其 provenance 完整获胜，
            # 不得让后条目的 reasoning 来源盖到前条目的字段上。
            continue
        models.append(mapped)
        seen.add(final_id)
        if record_reasoning:
            ctx.record_models_dev_source(mapped, entry)
    return models


__all__ = [
    "ApiCompatDefaults",
    "ProviderContext",
    "ProviderDefinition",
    "define_provider",
    "models_dev_models",
    "stamp_fields",
    "validate_pinned_model_type",
]
