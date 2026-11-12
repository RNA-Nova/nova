"""集成凭据店的文件安全测试（tmp_path，全程不触碰真实 auth.json）。"""

import json
import stat

import pytest
from nova_protocol import ApiKeyCredential

from tests.integration._helpers import auth as auth_helpers
from tests.integration._helpers.auth import AuthJsonCredentialStore

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _seal_process_provider_keys(monkeypatch):
    """文件安全单测即使带 integration 目录标记，也绝不读取进程 provider key。"""
    monkeypatch.setattr(auth_helpers, "builtin_providers", lambda: [])


async def test_file_store_does_not_read_process_provider_keys(tmp_path, monkeypatch):
    def fail_if_read(_provider_id):
        raise AssertionError("文件安全单测不得读取进程 provider key")

    monkeypatch.setattr(auth_helpers, "get_env_api_key", fail_if_read)
    store = AuthJsonCredentialStore(tmp_path / "auth.json")
    await store.ensure_loaded()
    assert store.list() == []


async def test_preserves_unknown_entries_and_merges_latest_file(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "test": {"type": "api_key", "key": "old"},
                "future": {"type": "future", "payload": {"keep": True}},
            }
        ),
        "utf-8",
    )
    store = AuthJsonCredentialStore(path)
    await store.ensure_loaded()

    # 模拟另一进程在本店加载后写入其他 provider。
    latest = json.loads(path.read_text("utf-8"))
    latest["other"] = {"type": "api_key", "key": "external"}
    path.write_text(json.dumps(latest), "utf-8")
    store.write(
        "test", ApiKeyCredential.model_validate({"type": "api_key", "key": "new"})
    )

    persisted = json.loads(path.read_text("utf-8"))
    assert persisted["test"] == {"type": "api_key", "key": "new"}
    assert persisted["other"]["key"] == "external"
    assert persisted["future"] == {
        "type": "future",
        "payload": {"keep": True},
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_malformed_existing_file_is_never_overwritten(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("not-json", "utf-8")
    store = AuthJsonCredentialStore(path)
    await store.ensure_loaded()
    with pytest.raises(json.JSONDecodeError):
        store.write(
            "test", ApiKeyCredential.model_validate({"type": "api_key", "key": "new"})
        )
    assert path.read_text("utf-8") == "not-json"


async def test_delete_removes_only_requested_entry(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "test": {"type": "api_key", "key": "old"},
                "future": {"type": "future", "keep": "yes"},
            }
        ),
        "utf-8",
    )
    store = AuthJsonCredentialStore(path)
    await store.ensure_loaded()
    store.delete("test")
    assert json.loads(path.read_text("utf-8")) == {
        "future": {"type": "future", "keep": "yes"}
    }
