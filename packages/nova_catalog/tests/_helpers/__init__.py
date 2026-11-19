"""nova_catalog 测试共享的小 fixture 与密封源。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nova_protocol import ModelFields

from nova_catalog.sources.load import CatalogSources
from nova_catalog.sources.models_dev import ModelsDevSource


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
