"""真实 generate.main 的产物、provider 边界与事务测试。"""

import json

import pytest
from nova_catalog import model_data as mdata
from nova_catalog.define import define_provider
from nova_catalog.generate import main
from nova_protocol import KnownApi
from tests._helpers import FakeSource, model_fields, raw_model, write_generated_fixture


def _definition(provider_id="test", **overrides):
    fields = {
        "id": provider_id,
        "name": provider_id,
        "base_url": "https://example.com/v1",
        "api": KnownApi.OPENAI_COMPLETIONS,
    }
    fields.update(overrides)
    return define_provider(**fields)


def _source(*provider_ids):
    return FakeSource(
        {
            provider_id: {"models": {"model-a": raw_model()}}
            for provider_id in provider_ids
        }
    )


def _tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_full_cli_generates_and_validates_minimal_tree(tmp_path):
    result = main(
        [],
        package_root=tmp_path,
        provider_definitions=[_definition()],
        source=_source("test"),
    )
    assert result == 0
    mdata.validate_generated_model_data(tmp_path)
    assert mdata.read_provider_ids(tmp_path) == ["test"]
    data = json.loads(
        (mdata.generated_data_dir(tmp_path) / "test.json").read_text("utf-8")
    )
    assert data["openai-completions"]["model-a"]["provider"] == "test"


def test_data_only_excludes_new_provider_from_existing_aggregator(tmp_path):
    write_generated_fixture(tmp_path)
    result = main(
        ["--data-only"],
        package_root=tmp_path,
        provider_definitions=[_definition(), _definition("new")],
        source=_source("test", "new"),
    )
    assert result == 0
    assert not (mdata.generated_data_dir(tmp_path) / "new.json").exists()
    assert mdata.read_provider_ids(tmp_path) == ["test"]


def test_full_generation_removes_deleted_provider_outputs(tmp_path):
    write_generated_fixture(
        tmp_path,
        {
            "keep": [model_fields(provider="keep")],
            "old": [model_fields(provider="old")],
        },
    )
    result = main(
        [],
        package_root=tmp_path,
        provider_definitions=[_definition("keep")],
        source=_source("keep"),
    )
    assert result == 0
    assert not (mdata.generated_data_dir(tmp_path) / "old.json").exists()
    assert not mdata.generated_shard_path(tmp_path, "old").exists()
    assert mdata.read_provider_ids(tmp_path) == ["keep"]


def test_data_only_rejects_provider_missing_from_definitions(tmp_path):
    write_generated_fixture(tmp_path)
    with pytest.raises(ValueError, match="run a full generation"):
        main(
            ["--data-only"],
            package_root=tmp_path,
            provider_definitions=[],
            source=FakeSource(),
        )


def test_post_commit_validation_failure_rolls_back_everything(monkeypatch, tmp_path):
    write_generated_fixture(
        tmp_path,
        {
            "test": [model_fields()],
            "old": [model_fields(provider="old")],
        },
    )
    before = _tree_bytes(tmp_path)
    original = mdata.validate_generated_model_data
    calls = 0

    def fail_second_validation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("post-commit mismatch")
        return original(*args, **kwargs)

    monkeypatch.setattr(mdata, "validate_generated_model_data", fail_second_validation)
    with pytest.raises(ValueError, match="post-commit mismatch"):
        main(
            [],
            package_root=tmp_path,
            provider_definitions=[_definition(), _definition("new")],
            source=_source("test", "new"),
        )
    assert _tree_bytes(tmp_path) == before


def test_staging_validation_failure_never_touches_formal_tree(monkeypatch, tmp_path):
    write_generated_fixture(tmp_path)
    before = _tree_bytes(tmp_path)

    def fail_validation(*_args, **_kwargs):
        raise ValueError("invalid staged data")

    monkeypatch.setattr(mdata, "validate_generated_model_data", fail_validation)
    with pytest.raises(ValueError, match="invalid staged data"):
        main(
            [],
            package_root=tmp_path,
            provider_definitions=[_definition()],
            source=_source("test"),
        )
    assert _tree_bytes(tmp_path) == before


def test_mixed_api_models_are_grouped_per_model(tmp_path):
    provider = _definition(
        pinned_models={
            "chat": model_fields("chat", api="openai-completions"),
            "message": model_fields("message", api="anthropic-messages"),
        }
    )
    result = main(
        [], package_root=tmp_path, provider_definitions=[provider], source=FakeSource()
    )
    assert result == 0
    data = json.loads(
        (mdata.generated_data_dir(tmp_path) / "test.json").read_text("utf-8")
    )
    assert set(data) == {"openai-completions", "anthropic-messages"}
    assert set(data["openai-completions"]) == {"chat"}
    assert set(data["anthropic-messages"]) == {"message"}


def test_strict_upstream_failure_never_touches_formal_tree(tmp_path):
    write_generated_fixture(tmp_path)
    before = _tree_bytes(tmp_path)
    with pytest.raises(RuntimeError, match="offline"):
        main(
            ["--strict"],
            package_root=tmp_path,
            provider_definitions=[_definition()],
            source=FakeSource(error=RuntimeError("offline")),
        )
    assert _tree_bytes(tmp_path) == before


def test_full_generation_removes_orphan_shard_not_in_old_aggregator(tmp_path):
    write_generated_fixture(tmp_path)
    orphan = mdata.generated_shard_path(tmp_path, "orphan")
    orphan.parent.mkdir(parents=True)
    orphan.write_text("# orphan\n", "utf-8")
    result = main(
        [],
        package_root=tmp_path,
        provider_definitions=[_definition()],
        source=_source("test"),
    )
    assert result == 0
    assert not orphan.exists()


def test_data_only_removes_stale_json(tmp_path):
    write_generated_fixture(tmp_path)
    stale = mdata.generated_data_dir(tmp_path) / "stale.json"
    stale.write_text("{}\n", "utf-8")
    result = main(
        ["--data-only"],
        package_root=tmp_path,
        provider_definitions=[_definition()],
        source=_source("test"),
    )
    assert result == 0
    assert not stale.exists()


def test_custom_api_is_preserved_in_generated_data(tmp_path):
    provider = _definition(pinned_models={"model-a": model_fields(api="custom-api")})
    result = main(
        [], package_root=tmp_path, provider_definitions=[provider], source=FakeSource()
    )
    assert result == 0
    data = json.loads(
        (mdata.generated_data_dir(tmp_path) / "test.json").read_text("utf-8")
    )
    assert data["custom-api"]["model-a"]["api"] == "custom-api"
