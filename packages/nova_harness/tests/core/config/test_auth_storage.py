"""
AuthStorage 测试。
"""

import pytest

from nova_harness.core.config.auth.storage import AuthStorage
from nova_harness.core.types.config.auth import ApiKeyCredential
from tests._helpers.auth_storage import auth_storage_in_memory


def test_auth_storage_in_memory_get_set_remove():
    storage = auth_storage_in_memory({})
    storage.set("openai", ApiKeyCredential(key="sk-test"))
    assert storage.has("openai") is True
    cred = storage.get("openai")
    assert cred is not None
    assert cred.type == "api_key"
    assert storage.get_api_key_sync("openai") == "sk-test"

    storage.remove("openai")
    assert storage.has("openai") is False


def test_auth_storage_runtime_override_wins():
    storage = auth_storage_in_memory(
        {"openai": {"type": "api_key", "key": "from-file"}}
    )
    storage.set_runtime_api_key("openai", "runtime-key")
    assert storage.get_api_key_sync("openai") == "runtime-key"

    storage.remove_runtime_api_key("openai")
    assert storage.get_api_key_sync("openai") == "from-file"


def test_auth_storage_fallback_resolver():
    storage = auth_storage_in_memory({})
    storage.set_fallback_resolver(
        lambda provider: "fallback" if provider == "custom" else None
    )
    assert storage.has_auth("custom") is True
    assert storage.get_api_key_sync("custom") == "fallback"


def test_auth_storage_env_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    storage = auth_storage_in_memory({})
    assert storage.get_api_key_sync("openai") == "env-key"


def test_auth_storage_list_and_all():
    storage = auth_storage_in_memory(
        {
            "openai": {"type": "api_key", "key": "k1"},
            "anthropic": {"type": "api_key", "key": "k2"},
        }
    )
    assert set(storage.list()) == {"openai", "anthropic"}
    assert storage.get_all()["openai"]["key"] == "k1"


# 同步辅助，避免每个测试都写 await
@pytest.fixture(autouse=True)
def _patch_sync_api_key():
    """为 AuthStorage 添加同步 get_api_key 辅助方法（仅测试使用）。"""
    if not hasattr(AuthStorage, "get_api_key_sync"):

        def _sync(self, provider):
            import asyncio

            return asyncio.run(self.get_api_key(provider))

        AuthStorage.get_api_key_sync = _sync
    yield
