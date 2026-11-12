"""nova_catalog 测试共享的小 fixture 与密封源。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from nova_catalog import model_data as mdata
from nova_catalog.sources.load import CatalogSources
from nova_catalog.sources.models_dev import ModelsDevSource
from nova_protocol import ModelFields


def raw_model(model_id: str = "model-a", **updates: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": model_id,
        "name": model_id,
        "type": "chat",
        "capabilities": {
            "tools": {"supported": True},
            "reasoning": {
                "supported": True,
                "effort": ["low", "high"],
            },
        },
        "limit": {"context": 128000, "output": 8192},
        "cost": {"text": {"input": {"price": 1}, "output": {"price": 2}}},
        "modalities": {"input": ["text"]},
    }
    entry.update(updates)
    return entry


def catalog_sources(
    models_dev: Optional[Dict[str, Any]] = None,
    *,
    openrouter_images: Optional[List[Dict[str, Any]]] = None,
    openrouter_decisions: Optional[List[Dict[str, Any]]] = None,
) -> CatalogSources:
    return CatalogSources(
        models_dev=models_dev or {},
        openrouter_images=openrouter_images or [],
        openrouter_decisions=openrouter_decisions or [],
    )


def raw_openrouter_image(
    model_id: str = "example/image", **updates: Any
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": model_id,
        "name": model_id,
        "architecture": {
            "input_modalities": ["text", "image"],
            "output_modalities": ["image"],
        },
        "pricing": {
            "prompt": "0.000001",
            "completion": "0.000002",
            "input_cache_read": "0.0000001",
            "input_cache_write": "0",
        },
    }
    entry.update(updates)
    return entry


def raw_openrouter_decision(
    model_id: str = "example/decision", **updates: Any
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": model_id,
        "name": model_id,
        "architecture": {
            "input_modalities": ["text"],
            "output_modalities": ["decisions"],
        },
        "context_length": 32000,
        "pricing": {"prompt": "0.000000042", "completion": "0"},
    }
    entry.update(updates)
    return entry


def model_fields(
    model_id: str = "model-a", provider: str = "test", **updates: Any
) -> ModelFields:
    fields: Dict[str, Any] = {
        "id": model_id,
        "name": model_id,
        "api": "openai-completions",
        "provider": provider,
        "base_url": "https://example.com/v1",
        "reasoning": True,
        "input_types": ["text"],
        "cost": {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0},
        "context_window": 128000,
        "max_tokens": 8192,
    }
    fields.update(updates)
    return ModelFields(**fields)


def image_fields(
    model_id: str = "model-a", provider: str = "test", **updates: Any
) -> ModelFields:
    fields: Dict[str, Any] = {
        "type": "image",
        "id": model_id,
        "name": model_id,
        "api": "openrouter-images",
        "provider": provider,
        "base_url": "https://example.com/v1",
        "input_types": ["text"],
        "output_types": ["image"],
        "cost": {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0},
    }
    fields.update(updates)
    return ModelFields(**fields)


def classifier_fields(
    model_id: str = "model-a", provider: str = "test", **updates: Any
) -> ModelFields:
    fields: Dict[str, Any] = {
        "type": "classifier",
        "id": model_id,
        "name": model_id,
        "api": "typesafe-system-one",
        "provider": provider,
        "base_url": "https://example.com/v1",
        "input_types": ["text"],
        "cost": {"input": 0.042, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 32000,
        "compat": {
            "image_input": "state-image-urls",
            "send_image_opt_in_header": False,
        },
    }
    fields.update(updates)
    return ModelFields(**fields)


class FakeSource(ModelsDevSource):
    """只实现 payload() 的密封 models.dev 源。"""

    def __init__(
        self,
        payload: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
    ) -> None:
        self._payload = payload or {}
        self._error = error
        self.calls = 0

    async def payload(self) -> Dict[str, Any]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._payload


def write_generated_fixture(
    root: Path, models_by_provider: Optional[Dict[str, list[ModelFields]]] = None
) -> Dict[str, Dict[str, str]]:
    """写一套与真实模板同构的最小正式树；返回其结构。"""
    models_by_provider = models_by_provider or {"test": [model_fields()]}
    provider_ids = sorted(models_by_provider)
    variables = {
        provider_id: (
            f"{provider_id.upper().replace('-', '_')}_MODELS",
            f"{provider_id.upper().replace('-', '_')}_IMAGE_MODELS",
            f"{provider_id.upper().replace('-', '_')}_CLASSIFIER_MODELS",
        )
        for provider_id in provider_ids
    }
    aggregator = mdata.generated_aggregator_path(root)
    aggregator.parent.mkdir(parents=True, exist_ok=True)
    imports = "".join(
        f"from nova_ai.providers.{provider_id.replace('-', '_')}.models import (\n"
        f"    {variables[provider_id][0]},\n"
        f"    {variables[provider_id][1]},\n"
        f"    {variables[provider_id][2]},\n"
        ")\n"
        for provider_id in provider_ids
    )
    chat_entries = ",\n".join(
        f'    "{provider_id}": {variables[provider_id][0]}'
        for provider_id in provider_ids
    )
    image_entries = ",\n".join(
        f'    "{provider_id}": {variables[provider_id][1]}'
        for provider_id in provider_ids
    )
    classifier_entries = ",\n".join(
        f'    "{provider_id}": {variables[provider_id][2]}'
        for provider_id in provider_ids
    )
    aggregator.write_text(
        f"{imports}\nPROVIDER_IDS = {provider_ids!r}\n\n"
        f"GENERATED_PROVIDER_MODELS = {{\n{chat_entries}\n}}\n\n"
        f"GENERATED_PROVIDER_IMAGE_MODELS = {{\n{image_entries}\n}}\n\n"
        f"GENERATED_PROVIDER_CLASSIFIER_MODELS = {{\n{classifier_entries}\n}}\n",
        "utf-8",
    )
    structure: Dict[str, Dict[str, str]] = {}
    file_contents: Dict[str, str] = {}
    data_dir = mdata.generated_data_dir(root)
    data_dir.mkdir(parents=True, exist_ok=True)
    for provider_id, models in models_by_provider.items():
        shard = mdata.generated_shard_path(root, provider_id)
        shard.parent.mkdir(parents=True, exist_ok=True)
        shard.write_text(
            "import json\n"
            "from importlib.resources import files\n"
            "from typing import Dict\n"
            "from nova_ai.model_catalog import flatten_chat_model_catalog, flatten_classifier_model_catalog, flatten_image_model_catalog\n"
            "from nova_protocol import ClassifierModel, ImageModel, Model\n\n"
            "_DATA = json.loads("
            f'(files("nova_ai.providers") / "data" / "{provider_id}.json").read_text("utf-8"))\n'
            f"{variables[provider_id][0]}: Dict[str, Model] = flatten_chat_model_catalog(\n"
            f"    {provider_id!r}, _DATA\n"
            ")\n"
            f"{variables[provider_id][1]}: Dict[str, ImageModel] = flatten_image_model_catalog(\n"
            f"    {provider_id!r}, _DATA\n"
            ")\n"
            f"{variables[provider_id][2]}: Dict[str, ClassifierModel] = flatten_classifier_model_catalog(\n"
            f"    {provider_id!r}, _DATA\n"
            ")\n",
            "utf-8",
        )
        groups: Dict[str, Dict[str, Any]] = {}
        structure[provider_id] = {}
        for fields in models:
            api = fields.get("api")
            model_id = fields.get("id")
            assert api is not None
            assert model_id is not None
            model_type = fields.get("type") or "chat"
            model_key = f"{model_type}:{model_id}"
            groups.setdefault(api, {})[model_key] = fields
            structure[provider_id][model_key] = api
        content = json.dumps(groups, sort_keys=True) + "\n"
        filename = f"{provider_id}.json"
        (data_dir / filename).write_text(content, "utf-8")
        file_contents[filename] = content
    manifest = mdata.create_model_data_manifest(
        structure, file_contents, "2026-01-01T00:00:00+00:00"
    )
    (data_dir / mdata.MODEL_DATA_MANIFEST_FILE).write_text(
        json.dumps(manifest, sort_keys=True) + "\n", "utf-8"
    )
    return structure
