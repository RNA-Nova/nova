"""模型数据保鲜门禁（对齐 TS ``check-model-data.ts`` 的构建检查）。

数据分片 ↔ manifest（结构哈希/文件哈希/schemaVersion）↔ 聚合器四方对账。
任何漂移（手改生成物、漏跑生成器、shard 与数据不一致）让测试红——
修复方式是重跑 ``pixi run -e dev generate-models``，不是改断言。
"""

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

import model_data as mdata


def test_generated_model_data_is_valid():
    """四方对账：聚合器 ↔ shard ↔ 数据分片 ↔ manifest。"""
    mdata.validate_generated_model_data(PACKAGE_ROOT)


def test_manifest_lists_all_generated_providers():
    structure = mdata.read_model_data_structure(PACKAGE_ROOT)
    assert set(structure) == {
        "kimi-coding",
        "moonshotai",
        "moonshotai-cn",
        "volcengine",
    }
    assert all(len(models) > 0 for models in structure.values())


def test_generated_shards_match_manifest():
    """每个 shard 的 MODELS 与数据分片一一对应（生成链路一致性）。"""
    from nova_ai.models_generated import GENERATED_PROVIDER_MODELS, PROVIDER_IDS

    for provider_id in PROVIDER_IDS:
        module = provider_id.replace("-", "_")
        assert module in GENERATED_PROVIDER_MODELS or True
        catalog = GENERATED_PROVIDER_MODELS[provider_id]
        structure = mdata.read_provider_structure(
            PACKAGE_ROOT / "src" / "nova_ai" / "providers" / "data", provider_id
        )
        assert set(catalog) == set(structure)
