"""
ModelRegistry 测试。
"""

import json
from pathlib import Path

import pytest

from nova_harness.core.config import AuthStorage, ModelRegistry


@pytest.fixture
def auth_with_volc_key():
    storage = AuthStorage.in_memory({})
    storage.set_runtime_api_key("volcengine", "volc-key")
    return storage


def test_model_registry_loads_builtin_volcengine(auth_with_volc_key):
    registry = ModelRegistry(auth_with_volc_key)
    model = registry.find("volcengine", "deepseek-v3-2-251201")
    assert model is not None
    assert model.provider == "volcengine"
    assert model.base_url == "https://ark.cn-beijing.volces.com/api/v3/"


def test_model_registry_get_available_filters_auth(auth_with_volc_key):
    registry = ModelRegistry(auth_with_volc_key)
    available = registry.get_available()
    providers = {m.provider for m in available}
    assert "volcengine" in providers


def test_model_registry_custom_models_json(tmp_path: Path):
    models_json = tmp_path / "models.json"
    models_json.write_text(
        json.dumps(
            {
                "providers": {
                    "custom": {
                        "base_url": "http://localhost:8000/v1",
                        "api_key": "local-key",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "local-model",
                                "name": "Local Model",
                                "context_window": 32000,
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    storage = AuthStorage.in_memory({})
    registry = ModelRegistry(storage, str(models_json))
    model = registry.find("custom", "local-model")
    assert model is not None
    assert model.base_url == "http://localhost:8000/v1"


def test_model_registry_provider_override_headers_and_compat(
    auth_with_volc_key, tmp_path: Path
):
    models_json = tmp_path / "models.json"
    models_json.write_text(
        json.dumps(
            {
                "providers": {
                    "volcengine": {
                        "headers": {"X-Custom": "yes"},
                        "model_overrides": {
                            "deepseek-v3-2-251201": {
                                "max_tokens": 9999,
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    registry = ModelRegistry(auth_with_volc_key, str(models_json))
    model = registry.find("volcengine", "deepseek-v3-2-251201")
    assert model is not None
    assert model.headers.get("X-Custom") == "yes"
    assert model.max_tokens == 9999


@pytest.mark.asyncio
async def test_model_registry_get_api_key_delegates_to_auth(auth_with_volc_key):
    registry = ModelRegistry(auth_with_volc_key)
    model = registry.find("volcengine", "deepseek-v3-2-251201")
    key = await registry.get_api_key(model)
    assert key == "volc-key"


def test_model_registry_register_provider_models_replaces_existing(auth_with_volc_key):
    registry = ModelRegistry(auth_with_volc_key)
    assert registry.find("volcengine", "deepseek-v3-2-251201") is not None

    registry.register_provider(
        "volcengine",
        {
            "base_url": "http://new",
            "api_key": "new-key",
            "api": "openai-completions",
            "models": [{"id": "only-model"}],
        },
    )
    assert registry.find("volcengine", "deepseek-v3-2-251201") is None
    assert registry.find("volcengine", "only-model") is not None


def test_model_registry_unregister_provider_restores_builtin(auth_with_volc_key):
    registry = ModelRegistry(auth_with_volc_key)
    registry.register_provider(
        "volcengine",
        {
            "base_url": "http://new",
            "api_key": "new-key",
            "api": "openai-completions",
            "models": [{"id": "only-model"}],
        },
    )
    registry.unregister_provider("volcengine")
    assert registry.find("volcengine", "deepseek-v3-2-251201") is not None
