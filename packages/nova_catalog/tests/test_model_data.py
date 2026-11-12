"""模型数据四方对账、数值与 manifest 边界测试。"""

import json
from typing import Any, Dict, cast

import pytest
from nova_catalog import model_data as mdata

from tests._helpers import classifier_fields, model_fields, write_generated_fixture


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
    "mutation",
    [
        "bool-window",
        "infinite-window",
        "bool-cost",
        "nan-tier",
        "empty-cost",
        "missing-cache-read",
        "string-reasoning",
        "string-cost",
        "alias-bool-tier",
        "fallback-bool-cost",
    ],
)
def test_invalid_raw_model_values_are_rejected(tmp_path, mutation):
    fields = model_fields()
    # 故意构造非法边界输入；TypedDict 的合法形状不适用于这些负例。
    mutable = cast(Dict[str, Any], fields)
    if mutation == "bool-window":
        mutable["context_window"] = True
    elif mutation == "infinite-window":
        mutable["max_tokens"] = float("inf")
    elif mutation == "bool-cost":
        mutable["cost"]["input"] = False
    elif mutation == "nan-tier":
        mutable["cost"]["tiers"] = [
            {
                "inputTokensAbove": float("nan"),
                "input": 1,
                "output": 1,
                "cache_read": 0,
                "cache_write": 0,
            }
        ]
    elif mutation == "empty-cost":
        mutable["cost"] = {}
    elif mutation == "missing-cache-read":
        mutable["cost"].pop("cache_read")
    elif mutation == "string-reasoning":
        mutable["reasoning"] = "false"
    elif mutation == "string-cost":
        mutable["cost"]["input"] = "1"
    elif mutation == "alias-bool-tier":
        mutable["cost"]["tiers"] = [
            {
                "input_tokens_above": True,
                "input": 1,
                "output": 1,
                "cache_read": 0,
                "cache_write": 0,
            }
        ]
    else:
        mutable["api"] = "anthropic-messages"
        mutable["compat"] = {
            "allowed_fallback_models": [
                {
                    "provider": "test",
                    "model": "fallback",
                    "cost": {
                        "input": False,
                        "output": 2,
                        "cache_read": 0,
                        "cache_write": 0,
                    },
                }
            ]
        }
    structure = write_generated_fixture(tmp_path, {"test": [fields]})
    with pytest.raises(ValueError):
        mdata.validate_model_data_directory(
            structure, mdata.generated_data_dir(tmp_path)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("compat", {"supports_reasoning_effort": "yes"}),
        ("compat", {"supports_strict_mod": True}),
        ("input_limits", {"images": {"resize": {"max_widht": 100}}}),
    ],
)
def test_nested_coercion_and_unknown_keys_are_rejected(tmp_path, field, value):
    fields = model_fields()
    mutable = cast(Dict[str, Any], fields)
    mutable[field] = value
    structure = write_generated_fixture(tmp_path, {"test": [fields]})
    with pytest.raises(ValueError):
        mdata.validate_model_data_directory(
            structure, mdata.generated_data_dir(tmp_path)
        )


def test_empty_generated_provider_mapping_is_rejected(tmp_path):
    write_generated_fixture(tmp_path)
    mdata.generated_aggregator_path(tmp_path).write_text(
        "PROVIDER_IDS = ['test']\nGENERATED_PROVIDER_MODELS = {}\n", "utf-8"
    )
    with pytest.raises(ValueError, match="provider mapping"):
        mdata.read_model_data_structure(tmp_path)


def test_invalid_shard_python_is_rejected(tmp_path):
    write_generated_fixture(tmp_path)
    mdata.generated_shard_path(tmp_path, "test").write_text("def broken(:\n", "utf-8")
    with pytest.raises(ValueError, match="invalid Python"):
        mdata.read_model_data_structure(tmp_path)


def test_shard_without_catalog_assignment_is_rejected(tmp_path):
    write_generated_fixture(tmp_path)
    mdata.generated_shard_path(tmp_path, "test").write_text("# no catalog\n", "utf-8")
    with pytest.raises(ValueError, match="TEST_MODELS"):
        mdata.read_model_data_structure(tmp_path)


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


def test_aggregator_import_module_and_names_are_checked(tmp_path):
    write_generated_fixture(tmp_path)
    aggregator = mdata.generated_aggregator_path(tmp_path)
    content = aggregator.read_text("utf-8")
    aggregator.write_text(
        content.replace(
            "from nova_ai.providers.test.models",
            "from nova_ai.providers.other.models",
        ),
        "utf-8",
    )
    with pytest.raises(ValueError, match="import"):
        mdata.read_model_data_structure(tmp_path)


def test_chat_entry_cannot_carry_image_output_types(tmp_path):
    structure = write_generated_fixture(
        tmp_path, {"test": [model_fields(output_types=["image"])]}
    )
    with pytest.raises(ValueError, match="output_types"):
        mdata.validate_model_data_directory(
            structure, mdata.generated_data_dir(tmp_path)
        )


def test_aggregator_rejects_relative_provider_import(tmp_path):
    write_generated_fixture(tmp_path)
    aggregator = mdata.generated_aggregator_path(tmp_path)
    content = aggregator.read_text("utf-8")
    aggregator.write_text(
        content.replace(
            "from nova_ai.providers.test.models", "from .nova_ai.providers.test.models"
        ),
        "utf-8",
    )
    with pytest.raises(ValueError, match="import"):
        mdata.read_model_data_structure(tmp_path)


def test_empty_shard_cannot_validate_nonempty_json(tmp_path):
    write_generated_fixture(tmp_path)
    shard = mdata.generated_shard_path(tmp_path, "test")
    shard.write_text("TEST_MODELS = {}\nTEST_IMAGE_MODELS = {}\n", "utf-8")
    with pytest.raises(ValueError, match="flatten"):
        mdata.read_model_data_structure(tmp_path)


def test_shard_rejects_aliased_required_import_bindings(tmp_path):
    write_generated_fixture(tmp_path)
    shard = mdata.generated_shard_path(tmp_path, "test")
    shard.write_text(
        shard.read_text("utf-8").replace("import json\n", "import json as j\n"),
        "utf-8",
    )
    with pytest.raises(ValueError, match="import"):
        mdata.read_model_data_structure(tmp_path)


def test_shard_rejects_imports_after_catalog_assignments(tmp_path):
    write_generated_fixture(tmp_path)
    shard = mdata.generated_shard_path(tmp_path, "test")
    lines = shard.read_text("utf-8").splitlines()
    lines.remove("import json")
    lines.append("import json")
    shard.write_text("\n".join(lines) + "\n", "utf-8")
    with pytest.raises(ValueError, match="import"):
        mdata.read_model_data_structure(tmp_path)


@pytest.mark.parametrize(
    "import_line",
    [
        "from typing import Dict",
        "from nova_protocol import ClassifierModel, ImageModel, Model",
    ],
)
def test_shard_requires_annotation_imports(tmp_path, import_line):
    write_generated_fixture(tmp_path)
    shard = mdata.generated_shard_path(tmp_path, "test")
    shard.write_text(shard.read_text("utf-8").replace(import_line + "\n", ""), "utf-8")
    with pytest.raises(ValueError, match="import"):
        mdata.read_model_data_structure(tmp_path)


def test_aggregator_rejects_import_after_mapping_reference(tmp_path):
    write_generated_fixture(tmp_path)
    aggregator = mdata.generated_aggregator_path(tmp_path)
    lines = aggregator.read_text("utf-8").splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("from nova_ai.providers")
    )
    end = lines.index(")", start)
    import_block = lines[start : end + 1]
    del lines[start : end + 1]
    lines.extend(import_block)
    aggregator.write_text("\n".join(lines) + "\n", "utf-8")
    with pytest.raises(ValueError, match="after.*reference"):
        mdata.read_model_data_structure(tmp_path)


def test_aggregator_rejects_import_rebinding_before_mapping(tmp_path):
    write_generated_fixture(tmp_path)
    aggregator = mdata.generated_aggregator_path(tmp_path)
    content = aggregator.read_text("utf-8")
    aggregator.write_text(
        content.replace("\nPROVIDER_IDS", "\nTEST_MODELS = {}\n\nPROVIDER_IDS", 1),
        "utf-8",
    )
    with pytest.raises(ValueError, match="rebound"):
        mdata.read_model_data_structure(tmp_path)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("Dict[str, Model]", "Dict[str, ImageModel]"),
        ("Dict[str, ImageModel]", "Dict[str, Model]"),
        ("TEST_MODELS: Dict[str, Model]", "TEST_MODELS: Dict"),
    ],
)
def test_shard_requires_precise_catalog_annotations(tmp_path, old, new):
    write_generated_fixture(tmp_path)
    shard = mdata.generated_shard_path(tmp_path, "test")
    shard.write_text(shard.read_text("utf-8").replace(old, new, 1), "utf-8")
    with pytest.raises(ValueError, match="annotation"):
        mdata.read_model_data_structure(tmp_path)


@pytest.mark.parametrize("rebinding", ["json = None\n", "Model = dict\n"])
def test_shard_rejects_rebound_required_imports(tmp_path, rebinding):
    write_generated_fixture(tmp_path)
    shard = mdata.generated_shard_path(tmp_path, "test")
    shard.write_text(shard.read_text("utf-8") + rebinding, "utf-8")
    with pytest.raises(ValueError, match="rebound"):
        mdata.read_model_data_structure(tmp_path)


def test_classifier_boolean_context_window_is_rejected(tmp_path):
    structure = write_generated_fixture(
        tmp_path, {"test": [classifier_fields(context_window=True)]}
    )
    with pytest.raises(ValueError, match="invalid context_window"):
        mdata.validate_model_data_directory(
            structure, mdata.generated_data_dir(tmp_path)
        )
