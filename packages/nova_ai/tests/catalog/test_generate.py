"""真实 generate.main 的产物、provider 边界与事务测试。"""

import json
import os

import pytest
from nova_ai.catalog import model_data as mdata
from nova_ai.catalog.define import define_provider, models_dev_models, stamp_fields
from nova_ai.catalog.generate import main
from nova_ai.catalog.sources.openrouter import openrouter_images_to_nova
from nova_protocol import KnownApi
from tests.catalog._helpers import (
    catalog_sources,
    classifier_fields,
    model_fields,
    raw_model,
    raw_openrouter_image,
    write_generated_fixture,
)


def _definition(provider_id="test", **overrides):
    base_url = overrides.pop("base_url", "https://example.com/v1")
    api = overrides.pop("api", KnownApi.OPENAI_COMPLETIONS)
    include_chat = overrides.pop("include_chat", True)
    image_source = overrides.pop("image_source", None)
    pinned = overrides.pop("pinned_models", None)

    def models(ctx):
        return models_dev_models(
            ctx,
            provider_id,
            api=api,
            base_url=base_url,
            pinned_models=pinned,
            **overrides,
        )

    def images(ctx):
        sourced = openrouter_images_to_nova(
            ctx.provider_id, ctx.sources.openrouter_images
        )
        if ctx.strict and not sourced:
            raise ValueError("OpenRouter returned no usable image models")
        return [
            stamp_fields(
                fields.get("id") or "",
                fields,
                provider_id=provider_id,
                base_url=base_url,
            )
            for fields in sourced
        ]

    return define_provider(
        id=provider_id,
        models=models if include_chat else None,
        images=images if image_source == "openrouter" else None,
    )


def _source(*provider_ids):
    return catalog_sources(
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


def _tree_directories(root):
    return {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()
    }


def test_full_cli_generates_and_validates_minimal_tree(tmp_path):
    result = main(
        [],
        package_root=tmp_path,
        provider_definitions=[_definition()],
        sources=_source("test"),
    )
    assert result == 0
    mdata.validate_generated_model_data(tmp_path)
    assert mdata.read_provider_ids(tmp_path) == ["test"]
    data = json.loads(
        (mdata.generated_data_dir(tmp_path) / "test.json").read_text("utf-8")
    )
    assert data["openai-completions"]["chat:model-a"]["provider"] == "test"
    shard = mdata.generated_shard_path(tmp_path, "test").read_text("utf-8")
    assert shard.count(".read_text(") == 1
    assert shard.count("json.loads(") == 1
    assert "def get_" not in shard
    assert "def list_" not in shard


def test_data_only_excludes_new_provider_from_existing_aggregator(tmp_path):
    write_generated_fixture(tmp_path)
    result = main(
        ["--data-only"],
        package_root=tmp_path,
        provider_definitions=[_definition(), _definition("new")],
        sources=_source("test", "new"),
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
        sources=_source("keep"),
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
            sources=catalog_sources(),
        )


def test_post_commit_validation_failure_rolls_back_everything(monkeypatch, tmp_path):
    """提交后的真实 JSON 校验失败时，所有已替换文件都恢复。"""
    write_generated_fixture(
        tmp_path,
        {
            "test": [model_fields()],
            "old": [model_fields(provider="old")],
        },
    )
    before = _tree_bytes(tmp_path)
    before_directories = _tree_directories(tmp_path)
    original_replace = os.replace

    def corrupt_committed_json(source, destination):
        original_replace(source, destination)
        if destination.suffix == ".json":
            destination.write_text("{broken", "utf-8")

    monkeypatch.setattr("nova_ai.catalog.generate.os.replace", corrupt_committed_json)
    with pytest.raises(ValueError, match="not valid JSON"):
        main(
            [],
            package_root=tmp_path,
            provider_definitions=[_definition(), _definition("new")],
            sources=_source("test", "new"),
        )
    assert _tree_bytes(tmp_path) == before
    assert _tree_directories(tmp_path) == before_directories


def test_keyboard_interrupt_during_commit_rolls_back_everything(monkeypatch, tmp_path):
    write_generated_fixture(tmp_path)
    before = _tree_bytes(tmp_path)
    original_replace = os.replace

    def interrupt_after_replace(source, destination):
        original_replace(source, destination)
        raise KeyboardInterrupt()

    monkeypatch.setattr("nova_ai.catalog.generate.os.replace", interrupt_after_replace)
    with pytest.raises(KeyboardInterrupt):
        main(
            [],
            package_root=tmp_path,
            provider_definitions=[_definition(), _definition("new")],
            sources=_source("test", "new"),
        )
    assert _tree_bytes(tmp_path) == before


def test_staging_validation_failure_never_touches_formal_tree(tmp_path):
    write_generated_fixture(tmp_path)
    before = _tree_bytes(tmp_path)
    provider = _definition(
        pinned_models={
            "model-a": model_fields(compat={"supports_strict_mode": "not-a-bool"})
        }
    )
    with pytest.raises(ValueError, match="Model schema"):
        main(
            [],
            package_root=tmp_path,
            provider_definitions=[provider],
            sources=catalog_sources(),
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
        [],
        package_root=tmp_path,
        provider_definitions=[provider],
        sources=catalog_sources(),
    )
    assert result == 0
    data = json.loads(
        (mdata.generated_data_dir(tmp_path) / "test.json").read_text("utf-8")
    )
    assert set(data) == {"openai-completions", "anthropic-messages"}
    assert set(data["openai-completions"]) == {"chat:chat"}
    assert set(data["anthropic-messages"]) == {"chat:message"}


def test_strict_missing_source_never_touches_formal_tree(tmp_path):
    write_generated_fixture(tmp_path)
    before = _tree_bytes(tmp_path)
    with pytest.raises(ValueError, match="missing a valid models slice"):
        main(
            ["--strict"],
            package_root=tmp_path,
            provider_definitions=[_definition()],
            sources=catalog_sources(),
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
        sources=_source("test"),
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
        sources=_source("test"),
    )
    assert result == 0
    assert not stale.exists()


def test_custom_api_is_preserved_in_generated_data(tmp_path):
    provider = _definition(pinned_models={"model-a": model_fields(api="custom-api")})
    result = main(
        [],
        package_root=tmp_path,
        provider_definitions=[provider],
        sources=catalog_sources(),
    )
    assert result == 0
    data = json.loads(
        (mdata.generated_data_dir(tmp_path) / "test.json").read_text("utf-8")
    )
    assert data["custom-api"]["chat:model-a"]["api"] == "custom-api"


def test_chat_and_image_entries_can_share_an_id(tmp_path):
    provider = _definition(
        image_source="openrouter",
        pinned_models={"model-a": model_fields()},
    )
    result = main(
        [],
        package_root=tmp_path,
        provider_definitions=[provider],
        sources=catalog_sources(openrouter_images=[raw_openrouter_image("model-a")]),
    )
    assert result == 0
    data = json.loads(
        (mdata.generated_data_dir(tmp_path) / "test.json").read_text("utf-8")
    )
    assert data["openai-completions"]["chat:model-a"]["type"] == "chat"
    assert data["openrouter-images"]["image:model-a"]["type"] == "image"
    mdata.validate_generated_model_data(tmp_path)


def test_chat_and_classifier_entries_can_share_an_id(tmp_path):
    provider = define_provider(
        id="test",
        models=lambda _ctx: [model_fields()],
        classifiers=lambda _ctx: [classifier_fields()],
    )
    result = main(
        [],
        package_root=tmp_path,
        provider_definitions=[provider],
        sources=catalog_sources(),
    )
    assert result == 0
    data = json.loads(
        (mdata.generated_data_dir(tmp_path) / "test.json").read_text("utf-8")
    )
    assert data["openai-completions"]["chat:model-a"]["type"] == "chat"
    classifier = data["typesafe-system-one"]["classifier:model-a"]
    assert classifier["type"] == "classifier"
    assert classifier["context_window"] == 32000
    mdata.validate_generated_model_data(tmp_path)
