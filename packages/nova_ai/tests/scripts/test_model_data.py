"""AI 聚合器 provider、shard 文件集合与默认路径的对账测试。"""

import pytest
import scripts.model_data as mdata

from tests.scripts._helpers import model_fields, write_generated_fixture


def test_default_package_root_targets_nova_ai_source_tree():
    root = mdata.default_package_root()
    assert root.name == "nova_ai"
    assert mdata.generated_aggregator_path(root).is_file()


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


@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        ("", "No PROVIDER_IDS"),
        ("PROVIDER_IDS = 'not-a-list'\n", "Invalid PROVIDER_IDS"),
        ("PROVIDER_IDS = ['test', 'test']\n", "Duplicate provider ids"),
    ],
)
def test_invalid_provider_ids_are_rejected(tmp_path, assignment, message):
    write_generated_fixture(tmp_path)
    mdata.generated_aggregator_path(tmp_path).write_text(assignment, "utf-8")
    with pytest.raises(ValueError, match=message):
        mdata.read_provider_ids(tmp_path)


def test_group_provider_model_data_returns_groups_and_structure():
    fields = model_fields()
    groups, structure = mdata.group_provider_model_data("test", [fields])
    assert groups == {"openai-completions": {"chat:model-a": fields}}
    assert structure == {"chat:model-a": "openai-completions"}
    with pytest.raises(ValueError, match="duplicate catalog entries"):
        mdata.group_provider_model_data("test", [fields, fields])
