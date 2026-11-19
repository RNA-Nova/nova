"""AI 模型数据分片、数值与 manifest 边界测试。"""

import json
from typing import Any, Dict, cast

import pytest
import scripts.model_data as mdata

from tests.scripts._helpers import (
    classifier_fields,
    model_fields,
    write_generated_fixture,
)


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


def test_chat_entry_cannot_carry_image_output_types(tmp_path):
    structure = write_generated_fixture(
        tmp_path, {"test": [model_fields(output_types=["image"])]}
    )
    with pytest.raises(ValueError, match="output_types"):
        mdata.validate_model_data_directory(
            structure, mdata.generated_data_dir(tmp_path)
        )


def test_classifier_boolean_context_window_is_rejected(tmp_path):
    structure = write_generated_fixture(
        tmp_path, {"test": [classifier_fields(context_window=True)]}
    )
    with pytest.raises(ValueError, match="invalid context_window"):
        mdata.validate_model_data_directory(
            structure, mdata.generated_data_dir(tmp_path)
        )
