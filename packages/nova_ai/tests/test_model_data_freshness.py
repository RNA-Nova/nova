"""模型数据一致性门禁（对齐 TS ``check-model-data.ts`` 的构建检查）。

数据分片 ↔ manifest（结构哈希/文件哈希/schemaVersion）↔ 聚合器四方对账。
手改任一生成物或 shard 与数据不一致会红；本门禁不声称能发现“修改了
构建定义但没重跑”——后者需要独立的输入指纹或生成差分机制。
"""

import json
from pathlib import Path

from nova_ai.catalog import model_data as mdata
from nova_protocol import ClassifierModel, ImageModel, Model

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def test_generated_model_data_is_valid():
    """四方对账：聚合器 ↔ shard ↔ 数据分片 ↔ manifest。"""
    mdata.validate_generated_model_data(PACKAGE_ROOT)


def test_manifest_lists_all_generated_providers():
    structure = mdata.read_model_data_structure(PACKAGE_ROOT)
    assert set(structure) == {
        "anthropic",
        "kimi-coding",
        "moonshotai",
        "moonshotai-cn",
        "openrouter",
        "volcengine",
    }
    assert all(len(models) > 0 for models in structure.values())


def test_generated_shards_match_manifest():
    """每个 shard 的三类目录都与数据分片的 typed key 一一对应。"""
    from nova_ai.models_generated import (
        GENERATED_PROVIDER_CLASSIFIER_MODELS,
        GENERATED_PROVIDER_IMAGE_MODELS,
        GENERATED_PROVIDER_MODELS,
        PROVIDER_IDS,
    )

    for provider_id in PROVIDER_IDS:
        assert provider_id in GENERATED_PROVIDER_MODELS
        assert provider_id in GENERATED_PROVIDER_IMAGE_MODELS
        assert provider_id in GENERATED_PROVIDER_CLASSIFIER_MODELS
        chat = GENERATED_PROVIDER_MODELS[provider_id]
        images = GENERATED_PROVIDER_IMAGE_MODELS[provider_id]
        classifiers = GENERATED_PROVIDER_CLASSIFIER_MODELS[provider_id]
        structure = mdata.read_provider_structure(
            PACKAGE_ROOT / "src" / "nova_ai" / "providers" / "data", provider_id
        )
        assert set(chat) == {
            key.removeprefix("chat:") for key in structure if key.startswith("chat:")
        }
        assert set(images) == {
            key.removeprefix("image:") for key in structure if key.startswith("image:")
        }
        assert set(classifiers) == {
            key.removeprefix("classifier:")
            for key in structure
            if key.startswith("classifier:")
        }


def test_generated_shards_preserve_every_json_field():
    """JSON → 运行时模型 → shard 全字段相等，不只比较模型 ID。"""
    from nova_ai.models_generated import (
        GENERATED_PROVIDER_CLASSIFIER_MODELS,
        GENERATED_PROVIDER_IMAGE_MODELS,
        GENERATED_PROVIDER_MODELS,
        PROVIDER_IDS,
    )

    mappings = {
        "chat": (GENERATED_PROVIDER_MODELS, Model),
        "image": (GENERATED_PROVIDER_IMAGE_MODELS, ImageModel),
        "classifier": (GENERATED_PROVIDER_CLASSIFIER_MODELS, ClassifierModel),
    }
    for provider_id in PROVIDER_IDS:
        path = mdata.generated_data_dir(PACKAGE_ROOT) / f"{provider_id}.json"
        groups = json.loads(path.read_text("utf-8"))
        for entries in groups.values():
            for typed_key, raw in entries.items():
                model_type, model_id = typed_key.split(":", 1)
                models, model_class = mappings[model_type]
                actual = models[provider_id][model_id]
                expected = model_class.model_validate(raw)
                assert actual.model_dump() == expected.model_dump()
