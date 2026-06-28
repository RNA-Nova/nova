"""
ModelRegistry 测试：覆盖自定义模型解析、provider/model 覆盖、
thinking_level_map 与 compat 透传。
"""

import json
import tempfile
from pathlib import Path

from nova_ai import OpenAICompletionsCompat

from nova_harness.core.config import AuthStorage, ModelRegistry


def _create_registry(models_data: dict):
    """在临时目录创建 AuthStorage + ModelRegistry，加载给定的 models.json 数据。"""
    with tempfile.TemporaryDirectory() as tmp:
        agent_dir = Path(tmp) / "agent"
        agent_dir.mkdir()
        models_path = agent_dir / "models.json"
        auth_path = agent_dir / "auth.json"
        models_path.write_text(json.dumps(models_data), encoding="utf-8")
        auth_path.write_text("{}", encoding="utf-8")

        auth_storage = AuthStorage.create(auth_path=auth_path)
        registry = ModelRegistry(auth_storage, models_json_path=str(models_path))
        return registry


def test_custom_model_thinking_level_map():
    registry = _create_registry(
        {
            "providers": {
                "custom": {
                    "base_url": "https://custom.example.com/v1",
                    "api_key": "sk-test",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "custom-model",
                            "thinking_level_map": {
                                "low": "effort-low",
                                "medium": "effort-medium",
                            },
                        }
                    ],
                }
            }
        }
    )

    model = registry.find("custom", "custom-model")
    assert model is not None
    assert model.thinking_level_map == {
        "low": "effort-low",
        "medium": "effort-medium",
    }


def test_provider_compat_override_applies_to_all_models():
    registry = _create_registry(
        {
            "providers": {
                "custom": {
                    "base_url": "https://custom.example.com/v1",
                    "api_key": "sk-test",
                    "api": "openai-completions",
                    "compat": {"thinking_format": "openai"},
                    "models": [{"id": "m1"}, {"id": "m2"}],
                }
            }
        }
    )

    for model_id in ("m1", "m2"):
        model = registry.find("custom", model_id)
        assert model is not None
        assert isinstance(model.compat, OpenAICompletionsCompat)
        assert model.compat.thinking_format == "openai"


def test_provider_thinking_level_map_default_for_models():
    registry = _create_registry(
        {
            "providers": {
                "custom": {
                    "base_url": "https://custom.example.com/v1",
                    "api_key": "sk-test",
                    "api": "openai-completions",
                    "thinking_level_map": {"low": "L", "medium": "M"},
                    "models": [
                        {"id": "m1"},
                        {"id": "m2", "thinking_level_map": {"high": "H"}},
                    ],
                }
            }
        }
    )

    m1 = registry.find("custom", "m1")
    assert m1.thinking_level_map == {"low": "L", "medium": "M"}

    m2 = registry.find("custom", "m2")
    assert m2.thinking_level_map == {"high": "H"}


def test_model_override_compat_and_thinking_level_map():
    registry = _create_registry(
        {
            "providers": {
                "custom": {
                    "base_url": "https://custom.example.com/v1",
                    "api_key": "sk-test",
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": "m1",
                            "compat": {"thinking_format": "deepseek"},
                            "thinking_level_map": {"low": "L"},
                        }
                    ],
                    "model_overrides": {
                        "m1": {
                            "compat": {"thinking_format": "openai"},
                            "thinking_level_map": {"medium": "M"},
                        }
                    },
                }
            }
        }
    )

    model = registry.find("custom", "m1")
    assert model.compat.thinking_format == "openai"
    assert model.thinking_level_map == {"medium": "M"}


def test_register_provider_with_compat_and_thinking_level_map():
    with tempfile.TemporaryDirectory() as tmp:
        agent_dir = Path(tmp) / "agent"
        agent_dir.mkdir()
        auth_path = agent_dir / "auth.json"
        auth_path.write_text("{}", encoding="utf-8")
        models_path = agent_dir / "models.json"
        models_path.write_text("{}", encoding="utf-8")

        auth_storage = AuthStorage.create(auth_path=auth_path)
        registry = ModelRegistry(auth_storage, models_json_path=str(models_path))

        registry.register_provider(
            "dynamic",
            {
                "base_url": "https://dynamic.example.com/v1",
                "api_key": "sk-dynamic",
                "api": "openai-completions",
                "compat": {"thinking_format": "openai"},
                "thinking_level_map": {"high": "H"},
                "models": [{"id": "d1"}],
            },
        )

        model = registry.find("dynamic", "d1")
        assert model is not None
        assert model.compat.thinking_format == "openai"
        assert model.thinking_level_map == {"high": "H"}


def test_provider_override_only_updates_existing_models():
    registry = _create_registry(
        {
            "providers": {
                "custom": {
                    "base_url": "https://custom.example.com/v1",
                    "api_key": "sk-test",
                    "api": "openai-completions",
                    "models": [{"id": "m1"}],
                }
            }
        }
    )

    registry.register_provider(
        "custom",
        {
            "compat": {"supports_store": True},
            "thinking_level_map": {"xhigh": "X"},
        },
    )

    model = registry.find("custom", "m1")
    assert model.compat.supports_store is True
    assert model.thinking_level_map == {"xhigh": "X"}
