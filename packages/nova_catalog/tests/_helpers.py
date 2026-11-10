"""nova_catalog 测试共享的小 fixture 与密封源。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from nova_catalog import model_data as mdata
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
    """写一套最小正式树；返回其结构。"""
    models_by_provider = models_by_provider or {"test": [model_fields()]}
    provider_ids = sorted(models_by_provider)
    aggregator = mdata.generated_aggregator_path(root)
    aggregator.parent.mkdir(parents=True, exist_ok=True)
    aggregator.write_text(f"PROVIDER_IDS = {provider_ids!r}\n", "utf-8")
    structure: Dict[str, Dict[str, str]] = {}
    file_contents: Dict[str, str] = {}
    data_dir = mdata.generated_data_dir(root)
    data_dir.mkdir(parents=True, exist_ok=True)
    for provider_id, models in models_by_provider.items():
        shard = mdata.generated_shard_path(root, provider_id)
        shard.parent.mkdir(parents=True, exist_ok=True)
        shard.write_text("# generated fixture\n", "utf-8")
        groups: Dict[str, Dict[str, Any]] = {}
        structure[provider_id] = {}
        for fields in models:
            api = fields["api"]
            model_id = fields["id"]
            groups.setdefault(api, {})[model_id] = fields
            structure[provider_id][model_id] = api
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
