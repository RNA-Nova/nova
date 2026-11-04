"""模型目录运行时机器（"目录数据 → ``Model``"的全部变换）。

- ``flatten_model_catalog``：数据分片（``providers/data/<provider>.json``，按
  ``{api 分组: {模型 id: 字段}}`` 组织）拍平成 ``{模型 id: Model}``——分片文件由
  ``catalog/generate.py`` 生成，运行时只消费、不改写；
- 运行时 finalize（``finalize_catalog_models``）：站拉取字段 → 盖戳 →
  ``Model`` 列表，归各家 refresh 与 harness 六件套治理皮消费；
- **修正语义唯一定义点在 nova_catalog**（``nova_catalog.engine`` 的
  ``apply_corrections``——构建/运行同一台引擎，本模块直引不另立）；
  知识唯一定义点在 ``nova_catalog.providers``（各 DEF——
  ``ProviderDefinition`` 结构满足者）；
- provider 独有的内容裁决（kimi managed 订阅源覆盖、火山 ark 在售筛选）
  不在本模块——归各家目录的 ``catalog.py`` 回调。

构建期专用流水线（``build_provider_models``/``source_models_for``）不在本
模块——归 ``nova_catalog`` 包（pi 同款：构建期世界独立成区）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, cast

from nova_catalog.define import ProviderDefinition
from nova_catalog.engine import stamp_fields
from nova_protocol import KnownApi, Model, ModelFields

__all__ = [
    "builtin_catalog_generated_at",
    "finalize_catalog_models",
    "flatten_model_catalog",
]

# ---------------------------------------------------------------------------
# 数据分片拍平（生成的 models.py shard 在 import 期消费）
# ---------------------------------------------------------------------------


def flatten_model_catalog(
    provider_id: str, groups: Dict[str, Dict[str, Dict[str, Any]]]
) -> Dict[str, Model]:
    """把 ``{api: {模型 id: 字段}}`` 拍平为 ``{模型 id: Model}``。

    - ``api`` 以分组键为准（字段里若带 ``api``，两者必须一致）；
    - ``provider`` 戳以入参为准；
    - 字段构造 ``Model`` 时做 pydantic 校验——脏数据在导入期即报错。
    """
    models: Dict[str, Model] = {}
    for api, group in groups.items():
        try:
            KnownApi(api)
        except ValueError:
            raise ValueError(f"Unknown api group: {api!r}") from None
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


# ---------------------------------------------------------------------------
# 修正引擎（约定发现：知识归各 provider 目录 corrections.py）
# ---------------------------------------------------------------------------


def finalize_catalog_models(
    provider_id: str,
    base_url: Optional[str],
    models_fields: List[ModelFields],
    *,
    knowledge: Optional[ProviderDefinition] = None,
) -> List[Model]:
    """站拉取字段 → 修正 → 盖戳 → ``Model`` 列表（运行时 finalize 单点）。

    盖戳素材（``provider_id``/``base_url``）与修正知识（``knowledge``——
    ``nova_catalog`` 的 ``ProviderDefinition``）全部由调用方递交——引擎
    不查找、不注册、不含任何单家逻辑；provider 独有的内容裁决（kimi managed
    订阅源覆盖、火山 ark 在售筛选）归各家回调。钉住模型（``pinned_models``）与站拉取合并，
    同 id 站拉取胜。
    """
    raw: Dict[str, ModelFields] = {}
    for fields in models_fields:
        model_id = fields.get("id")
        if not model_id:
            continue
        # 拷贝（apply_corrections 原地改，不污染调用方持有的字段包）
        raw[model_id] = cast(ModelFields, dict(fields))

    pinned = knowledge.pinned_models if knowledge is not None else {}
    models: List[Model] = []

    def _finalize(model_id: str, fields: ModelFields) -> Model:
        # 修正+盖戳归 nova_catalog.engine.stamp_fields（单一实现点）；
        # 本层只补 Model 构造（运行期产物形态——model_validate 即目录边界
        # 一次性校验入口，TypedDict 声明形状在此验收）
        return Model.model_validate(
            stamp_fields(
                knowledge,
                model_id,
                fields,
                provider_id=provider_id,
                base_url=base_url,
            )
        )

    for model_id, fields in raw.items():
        models.append(_finalize(model_id, fields))
    for model_id, fields in pinned.items():
        if model_id not in raw:
            models.append(_finalize(model_id, cast(ModelFields, dict(fields))))
    return models


def builtin_catalog_generated_at() -> Optional[int]:
    """基线目录的生成时间（epoch ms；来自 data/.manifest.json，缺失返回 None）。

    远程目录物化的新鲜度竞速锚点：基线比运行时缓存新时，缓存 overlay
    让位（对齐 TS getBuiltinModelDataGeneratedAt）。包资源经
    importlib.resources 访问，不依赖 __file__ 安装布局（zip 安装安全）。
    """
    import json
    from datetime import datetime
    from importlib.resources import files

    manifest_ref = files("nova_ai.providers") / "data" / ".manifest.json"
    try:
        manifest = json.loads(manifest_ref.read_text("utf-8"))
        return int(datetime.fromisoformat(manifest["generatedAt"]).timestamp() * 1000)
    except Exception:
        return None
