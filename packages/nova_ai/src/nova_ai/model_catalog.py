"""模型目录扁平化助手（对齐 TS ``src/model-catalog.ts`` 的 ``flattenModelCatalog``）。

数据分片（``providers/data/<provider>.json``）按 ``{api 分组: {模型 id: 字段}}``
组织——同一 provider 的不同模型可能走不同 API 协议。本助手把嵌套分组拍平成
``{模型 id: Model}``，并盖上 ``api`` / ``provider`` 戳。

分片文件（``providers/<provider>/models.py``）由 ``scripts/generate_models.py``
生成，运行时只消费、不改写。
"""

from __future__ import annotations

from typing import Any, Dict

from .types.enums import KnownApi
from .types.model import Model

__all__ = ["flatten_model_catalog"]


def flatten_model_catalog(provider_id: str, groups: Dict[str, Any]) -> Dict[str, Model]:
    """把 ``{api: {模型 id: 字段}}`` 拍平为 ``{模型 id: Model}``。

    - ``api`` 以分组键为准（字段里若带 ``api``，两者必须一致）；
    - ``provider`` 戳以入参为准；
    - 字段构造 ``Model`` 时做 pydantic 校验——脏数据在导入期即报错。
    """
    models: Dict[str, Model] = {}
    for api, group in groups.items():
        if api not in KnownApi._value2member_map_:
            raise ValueError(f"Unknown api group: {api!r}")
        for model_id, fields in group.items():
            fields = dict(fields)
            if fields.get("id") != model_id:
                raise ValueError(
                    f"{provider_id}/{model_id}: data id mismatch: {fields.get('id')!r}"
                )
            declared_api = fields.get("api")
            if declared_api is not None and declared_api != api:
                raise ValueError(
                    f"{provider_id}/{model_id}: data api {declared_api!r} != group {api!r}"
                )
            fields["api"] = api
            fields["provider"] = provider_id
            models[model_id] = Model(**fields)
    return models
