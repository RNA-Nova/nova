"""
AuthStorage 测试。
"""

import pytest
from nova_protocol import ApiKeyCredential, OAuthCredential, RedactedString

from nova_harness.config.auth.storage import AuthStorage
from tests._helpers.auth_storage import auth_storage_in_memory


def test_write_read_delete_roundtrip():
    storage = auth_storage_in_memory({})
    storage.write("openai", ApiKeyCredential(key=RedactedString("sk-test")))
    assert storage.has("openai") is True
    cred = storage.read("openai")
    assert cred is not None
    assert isinstance(cred, ApiKeyCredential)
    assert cred.type == "api_key"
    assert cred.key == "sk-test"

    storage.delete("openai")
    assert storage.has("openai") is False


def test_list_returns_credential_metadata():
    storage = auth_storage_in_memory(
        {
            "openai": {"type": "api_key", "key": "k1"},
            "anthropic": {"type": "api_key", "key": "k2"},
        }
    )
    infos = storage.list()
    assert {info.provider_id for info in infos} == {"openai", "anthropic"}


def test_has_auth_covers_stored_runtime_and_env(monkeypatch):
    storage = auth_storage_in_memory({"stored-p": {"type": "api_key", "key": "k"}})
    assert storage.has_auth("stored-p") is True

    storage.set_runtime_api_key("runtime-p", "k")
    assert storage.has_auth("runtime-p") is True

    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    assert storage.has_auth("openai") is True
    assert storage.has_auth("no-such-provider") is False


# ---------------------------------------------------------------------------
# CredentialStore protocol
# ---------------------------------------------------------------------------


def test_credential_store_api_key_read_resolves_env(monkeypatch):
    """存储的 ``$ENV`` 引用在 read 时解析为环境变量值。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    storage = auth_storage_in_memory({})
    storage.write("openai", ApiKeyCredential(key=RedactedString("$OPENAI_API_KEY")))

    cred = storage.read("openai")
    assert cred is not None
    assert cred.type == "api_key"
    assert cred.key == "sk-from-env"


def test_credential_store_api_key_literal_passthrough():
    """裸字符串按字面量处理，不再隐式查找同名环境变量。"""
    storage = auth_storage_in_memory({})
    storage.write("openai", ApiKeyCredential(key=RedactedString("sk-literal")))

    cred = storage.read("openai")
    assert cred is not None
    assert isinstance(cred, ApiKeyCredential)
    assert cred.key == "sk-literal"


def test_credential_store_oauth_roundtrip():
    storage = auth_storage_in_memory({})
    cred = OAuthCredential(
        access=RedactedString("a"), refresh=RedactedString("r"), expires=1
    )
    storage.write("codex", cred)

    read = storage.read("codex")
    assert read is not None
    assert read.type == "oauth"
    assert isinstance(read, OAuthCredential)
    assert read.access == "a"


def test_credential_store_list_and_delete():
    storage = auth_storage_in_memory({})
    storage.write("openai", ApiKeyCredential(key=RedactedString("k1")))
    storage.write(
        "codex",
        OAuthCredential(
            access=RedactedString("a"), refresh=RedactedString("r"), expires=1
        ),
    )

    infos = storage.list()
    assert {info.provider_id for info in infos} == {"openai", "codex"}

    storage.delete("openai")
    assert storage.read("openai") is None
    assert storage.read("codex") is not None


def test_credential_store_runtime_override_takes_priority_on_read():
    """对齐 TS RuntimeCredentials：runtime override 在 read/list 中优先于存储 credential。"""
    storage = auth_storage_in_memory({"openai": {"type": "api_key", "key": "stored"}})
    storage.set_runtime_api_key("openai", "runtime")

    cred = storage.read("openai")
    assert isinstance(cred, ApiKeyCredential)
    assert cred.key == "runtime"


def test_credential_store_list_includes_runtime_overrides():
    storage = auth_storage_in_memory({"openai": {"type": "api_key", "key": "stored"}})
    storage.set_runtime_api_key("custom-provider", "runtime")

    infos = storage.list()
    by_provider = {info.provider_id: info for info in infos}
    assert by_provider["openai"].type == "api_key"
    assert by_provider["custom-provider"].type == "api_key"


def test_credential_store_delete_clears_runtime_override():
    storage = auth_storage_in_memory({})
    storage.set_runtime_api_key("openai", "runtime")

    storage.delete("openai")
    assert storage.read("openai") is None


# ---------------------------------------------------------------------------
# File persistence
# ---------------------------------------------------------------------------


def test_auth_storage_persists_oauth_to_file(tmp_path):
    auth_path = tmp_path / "auth.json"
    storage = AuthStorage.create(auth_path)
    cred = OAuthCredential(
        access=RedactedString("access-token"),
        refresh=RedactedString("refresh-token"),
        expires=9999999999,
    )
    storage.write("codex", cred)

    # 重新从文件加载
    reloaded = AuthStorage.create(auth_path)
    read = reloaded.read("codex")
    assert read is not None
    assert read.type == "oauth"
    assert read.access == "access-token"
    assert read.refresh == "refresh-token"


def test_auth_storage_write_updates_file(tmp_path):
    auth_path = tmp_path / "auth.json"
    storage = AuthStorage.create(auth_path)
    expired = OAuthCredential(
        access=RedactedString("old"), refresh=RedactedString("r"), expires=1
    )
    storage.write("codex", expired)

    storage.write(
        "codex",
        OAuthCredential(
            access=RedactedString("new"),
            refresh=RedactedString("r"),
            expires=9999999999,
        ),
    )

    reloaded = AuthStorage.create(auth_path)
    read = reloaded.read("codex")
    assert isinstance(read, OAuthCredential)
    assert read.access == "new"


def test_read_uses_credential_env_for_resolution(monkeypatch):
    """credential 自带的 env 参与 $VAR 解析（优先于进程环境）。"""
    monkeypatch.setenv("SHARED_KEY", "from-process")
    storage = auth_storage_in_memory(
        {
            "p": {
                "type": "api_key",
                "key": "$SHARED_KEY",
                "env": {"SHARED_KEY": "from-credential"},
            }
        }
    )
    cred = storage.read("p")
    assert isinstance(cred, ApiKeyCredential)
    assert cred.key == "from-credential"


def test_read_unresolvable_key_becomes_none():
    """$VAR 解析失败时 key 置 None（下游回落 env 链），而非返回原文。"""
    storage = auth_storage_in_memory(
        {"p": {"type": "api_key", "key": "$NOVA_DEFINITELY_MISSING"}}
    )
    cred = storage.read("p")
    assert cred is not None
    assert isinstance(cred, ApiKeyCredential)
    assert cred.key is None


def test_write_rereads_file_inside_lock(tmp_path):
    """锁内重读：构造后由外部进程写入的 credential 不会被 write 覆盖丢失。"""
    import json as _json

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(_json.dumps({}), encoding="utf-8")
    storage = AuthStorage.create(auth_path=auth_path)

    # 模拟另一个进程写入
    auth_path.write_text(
        _json.dumps({"other": {"type": "api_key", "key": "k-other"}}),
        encoding="utf-8",
    )

    storage.write("mine", ApiKeyCredential(key=RedactedString("k-mine")))

    on_disk = _json.loads(auth_path.read_text(encoding="utf-8"))
    assert "other" in on_disk  # 外部写入未被覆盖
    assert on_disk["mine"]["key"] == "k-mine"


def test_delete_rereads_file_inside_lock(tmp_path):
    import json as _json

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        _json.dumps({"a": {"type": "api_key", "key": "ka"}}), encoding="utf-8"
    )
    storage = AuthStorage.create(auth_path=auth_path)

    # 模拟另一个进程写入
    auth_path.write_text(
        _json.dumps(
            {
                "a": {"type": "api_key", "key": "ka"},
                "b": {"type": "api_key", "key": "kb"},
            }
        ),
        encoding="utf-8",
    )

    storage.delete("a")
    on_disk = _json.loads(auth_path.read_text(encoding="utf-8"))
    assert "a" not in on_disk
    assert on_disk["b"]["key"] == "kb"


def test_delete_preserves_unparseable_entries(tmp_path):
    """删除某 provider 时，无法解析的凭据条目必须原样保留（不静默抹掉）。

    回归：此前 parse 静默跳过无效条目，重写时丢失——登出一个 provider
    会把另一个写法不标准（如 type: "apiKey"）的 provider 凭据永久删除。
    """
    import json as _json

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        _json.dumps(
            {
                "victim": {"type": "apiKey", "key": "legacy-format"},  # 非标准型
                "mine": {"type": "api_key", "key": "k-mine"},
            }
        ),
        encoding="utf-8",
    )
    storage = AuthStorage.create(auth_path=auth_path)

    storage.delete("mine")
    on_disk = _json.loads(auth_path.read_text(encoding="utf-8"))
    assert "mine" not in on_disk
    assert on_disk["victim"] == {"type": "apiKey", "key": "legacy-format"}  # 原样保留


def test_write_preserves_unparseable_entries(tmp_path):
    """write 写入同样保留无法解析的条目。"""
    import json as _json

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        _json.dumps({"victim": {"type": "apiKey", "key": "legacy"}}), encoding="utf-8"
    )
    storage = AuthStorage.create(auth_path=auth_path)

    from nova_protocol.auth import ApiKeyCredential

    storage.write(
        "new-provider", ApiKeyCredential(type="api_key", key=RedactedString("new-key"))
    )
    on_disk = _json.loads(auth_path.read_text(encoding="utf-8"))
    assert on_disk["new-provider"]["key"] == "new-key"
    assert on_disk["victim"] == {"type": "apiKey", "key": "legacy"}


def test_read_picks_up_external_file_changes(tmp_path):
    """跨进程可见性：外部写入（模拟另一 nova 进程刷新凭据）后，read 经
    revision 比对重载——per-provider 锁内复查的数据源必须是文件现状。"""
    import json as _json

    auth_path = tmp_path / "auth.json"
    storage = AuthStorage.create(auth_path)
    storage.write("mine", ApiKeyCredential(key=RedactedString("k-mine")))

    # 模拟另一个进程写入
    auth_path.write_text(
        _json.dumps({"other": {"type": "api_key", "key": "k-other"}}), encoding="utf-8"
    )

    cred = storage.read("other")
    assert isinstance(cred, ApiKeyCredential)
    assert cred.key == "k-other"


def test_write_failure_keeps_memory_and_file_consistent(tmp_path, monkeypatch):
    """写盘失败时内存快照不得先行更新（失败路径内存与文件不分叉）。"""
    auth_path = tmp_path / "auth.json"
    storage = AuthStorage.create(auth_path)
    storage.write("mine", ApiKeyCredential(key=RedactedString("k-before")))

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", _boom)
    with pytest.raises(OSError):
        storage.write("mine", ApiKeyCredential(key=RedactedString("k-after")))

    read = storage.read("mine")
    assert isinstance(read, ApiKeyCredential)
    assert read.key == "k-before"  # 内存仍是写前快照
    import json as _json

    on_disk = _json.loads(auth_path.read_text(encoding="utf-8"))
    assert on_disk["mine"]["key"] == "k-before"  # 文件也是
