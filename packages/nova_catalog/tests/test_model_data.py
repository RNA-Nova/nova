"""模型数据四方对账、数值与 manifest 边界测试。"""

import json

import pytest
from nova_catalog import model_data as mdata
from tests._helpers import model_fields, write_generated_fixture


def test_minimal_generated_tree_is_valid(tmp_path):
    structure = write_generated_fixture(tmp_path)
    assert mdata.read_model_data_structure(tmp_path) == structure
    mdata.validate_model_data_directory(structure, mdata.generated_data_dir(tmp_path))


def test_missing_or_extra_shard_is_rejected(tmp_path):
    write_generated_fixture(tmp_path)
    shard = mdata.generated_shard_path(tmp_path, "test")
    shard.unlink()
    with pytest.raises(ValueError, match="aggregator and provider shards"):
        mdata.read_model_data_structure(tmp_path)
    write_generated_fixture(tmp_path)
    extra = mdata.generated_shard_path(tmp_path, "extra")
    extra.parent.mkdir(parents=True)
    extra.write_text("# stale\n", "utf-8")
    with pytest.raises(ValueError, match="aggregator and provider shards"):
        mdata.read_model_data_structure(tmp_path)


def test_invalid_generation_timestamp_is_rejected(tmp_path):
    structure = write_generated_fixture(tmp_path)
    data_dir = mdata.generated_data_dir(tmp_path)
    path = data_dir / mdata.MODEL_DATA_MANIFEST_FILE
    manifest = json.loads(path.read_text("utf-8"))
    manifest["generatedAt"] = "not-a-date"
    path.write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="generation timestamp"):
        mdata.validate_model_data_directory(structure, data_dir)


@pytest.mark.parametrize(
    "mutation", ["bool-window", "infinite-window", "bool-cost", "nan-tier"]
)
def test_non_finite_or_boolean_numeric_fields_are_rejected(tmp_path, mutation):
    fields = model_fields()
    if mutation == "bool-window":
        fields["context_window"] = True
    elif mutation == "infinite-window":
        fields["max_tokens"] = float("inf")
    elif mutation == "bool-cost":
        fields["cost"]["input"] = False
    else:
        fields["cost"]["tiers"] = [
            {
                "inputTokensAbove": float("nan"),
                "input": 1,
                "output": 1,
                "cache_read": 0,
                "cache_write": 0,
            }
        ]
    structure = write_generated_fixture(tmp_path, {"test": [fields]})
    # 非有限数在 JSON 边界即拒绝；其余进入字段校验。
    with pytest.raises(ValueError):
        mdata.validate_model_data_directory(
            structure, mdata.generated_data_dir(tmp_path)
        )


def test_manifest_boolean_schema_version_is_rejected(tmp_path):
    structure = write_generated_fixture(tmp_path)
    path = mdata.generated_data_dir(tmp_path) / mdata.MODEL_DATA_MANIFEST_FILE
    manifest = json.loads(path.read_text("utf-8"))
    manifest["schemaVersion"] = True
    path.write_text(json.dumps(manifest), "utf-8")
    with pytest.raises(ValueError, match="model data schema"):
        mdata.validate_model_data_directory(
            structure, mdata.generated_data_dir(tmp_path)
        )
