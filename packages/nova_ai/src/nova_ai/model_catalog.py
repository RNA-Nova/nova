"""模型目录运行时机器（typed-key → Model/ImageModel/ClassifierModel）。

- ``flatten_chat/image/classifier_model_catalog``：把生成的 JSON 分片拍平成
  对应操作目录；
- ``finalize_catalog_models``：把目录站字段物化为混合目录，负责身份盖戳、
  去重、effort 和图像默认限制。pi.dev 等成品源不重复执行 provider 修正；
  models.dev 原料源的专属修正在进入本函数前由 provider 的 materialize
  回调完成。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, cast

from nova_ai.catalog.define import stamp_fields
from nova_ai.catalog.input_limits import apply_image_resize_defaults
from nova_ai.catalog.reasoning import apply_models_dev_effort
from nova_protocol import (
    AnyModel,
    ClassifierModel,
    ImageModel,
    Model,
    ModelFields,
    ModelType,
)

__all__ = [
    "builtin_catalog_generated_at",
    "finalize_catalog_models",
    "flatten_chat_model_catalog",
    "flatten_classifier_model_catalog",
    "flatten_image_model_catalog",
]


def _flatten_model_catalog(
    provider_id: str,
    groups: Mapping[str, Mapping[str, Mapping[str, Any]]],
    model_type: ModelType,
) -> Dict[str, Any]:
    """按 typed key 过滤一种操作，再经对应 Pydantic 模型验收。"""
    models: Dict[str, Any] = {}
    for api, group in groups.items():
        for model_key, raw_fields in group.items():
            entry_type, separator, model_id = model_key.partition(":")
            if not separator or entry_type != model_type:
                continue
            fields = dict(raw_fields)
            if fields.get("id") != model_id:
                raise ValueError(
                    f"{provider_id}/{model_id}: data id mismatch: {fields.get('id')!r}"
                )
            declared_api = fields.get("api")
            if declared_api is not None and declared_api != api:
                raise ValueError(
                    f"{provider_id}/{model_id}: data api {declared_api!r} != group {api!r}"
                )
            fields["type"] = model_type
            fields["api"] = api
            fields["provider"] = provider_id
            if model_type == "image":
                models[model_id] = ImageModel(**fields)
            elif model_type == "classifier":
                models[model_id] = ClassifierModel(**fields)
            else:
                models[model_id] = Model(**fields)
    return models


def flatten_chat_model_catalog(
    provider_id: str, groups: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> Dict[str, Model]:
    """拍平 ``chat:<id>`` 条目；其他操作不会误入聊天目录。"""
    return cast(Dict[str, Model], _flatten_model_catalog(provider_id, groups, "chat"))


def flatten_image_model_catalog(
    provider_id: str, groups: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> Dict[str, ImageModel]:
    """拍平 ``image:<id>`` 条目并校验图像输出模态。"""
    return cast(
        Dict[str, ImageModel], _flatten_model_catalog(provider_id, groups, "image")
    )


def flatten_classifier_model_catalog(
    provider_id: str, groups: Mapping[str, Mapping[str, Mapping[str, Any]]]
) -> Dict[str, ClassifierModel]:
    """拍平 ``classifier:<id>`` 条目并校验分类模型形状。"""
    return cast(
        Dict[str, ClassifierModel],
        _flatten_model_catalog(provider_id, groups, "classifier"),
    )


def finalize_catalog_models(
    provider_id: str,
    base_url: Optional[str],
    models_fields: Sequence[ModelFields],
    *,
    source_reasoning: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[AnyModel]:
    """站字段 → 身份盖戳 → 分操作混合目录（运行时单点）。

    去重键是 ``(type, id)``；同类型冲突由后到站条目胜出。原料修正已在
    更前面的 provider materialize 阶段完成。
    """
    raw: Dict[tuple[str, str], ModelFields] = {}
    for fields in models_fields:
        model_id = fields.get("id")
        model_type = fields["type"] if "type" in fields else "chat"
        if (
            not isinstance(model_id, str)
            or not model_id
            or model_type not in ("chat", "image", "classifier")
        ):
            continue
        raw[(model_type, model_id)] = cast(ModelFields, dict(fields))

    models: List[AnyModel] = []
    for (model_type, model_id), fields in raw.items():
        source_base_url = fields.get("base_url")
        base_url_override = (
            None if isinstance(source_base_url, str) and source_base_url else base_url
        )
        stamped = stamp_fields(
            model_id,
            fields,
            provider_id=provider_id,
            base_url=base_url_override,
        )
        if model_type == "chat":
            apply_models_dev_effort(stamped, (source_reasoning or {}).get(model_id))
        if model_type in ("chat", "image"):
            # classifier 与 pi 一致：静态构建和远程成品都不额外补默认缩放。
            apply_image_resize_defaults(stamped)
        stamped["type"] = model_type
        if model_type == "image":
            models.append(ImageModel.model_validate(stamped))
        elif model_type == "classifier":
            models.append(ClassifierModel.model_validate(stamped))
        else:
            models.append(Model.model_validate(stamped))
    return models


def builtin_catalog_generated_at() -> Optional[int]:
    """基线目录的生成时间（epoch ms；来自 data/.manifest.json，缺失返回 None）。"""
    import json
    from datetime import datetime
    from importlib.resources import files

    manifest_ref = files("nova_ai.providers") / "data" / ".manifest.json"
    try:
        manifest = json.loads(manifest_ref.read_text("utf-8"))
        return int(datetime.fromisoformat(manifest["generatedAt"]).timestamp() * 1000)
    except Exception:
        return None
