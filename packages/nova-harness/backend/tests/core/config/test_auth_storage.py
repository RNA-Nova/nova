"""
AuthStorage 测试。
"""

import pytest
from nova_ai.types.auth import ApiKeyCredential, OAuthCredential

from nova_harness.core.config.auth.storage import AuthStorage
from tests._helpers.auth_storage import auth_storage_in_memory


@pytest.mark.asyncio
async def test_modify_read_delete_roundtrip():
    storage = auth_storage_in_memory({})
    await storage.modify("openai", lambda _c: _just(ApiKeyCredential(key="sk-test")))
    assert storage.has("openai") is True
    cred = await storage.read("openai")
    assert cred is not None
    assert cred.type == "api_key"
    assert cred.key == "sk-test"

    await storage.delete("openai")
    assert storage.has("openai") is False


@pytest.mark.asyncio
async def test_list_returns_credential_metadata():
    storage = auth_storage_in_memory(
        {
            "openai": {"type": "api_key", "key": "k1"},
            "anthropic": {"type": "api_key", "key": "k2"},
        }
    )
    infos = await storage.list()
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


@pytest.mark.asyncio
async def test_credential_store_api_key_read_resolves_env(monkeypatch):
    """存储的 ``$ENV`` 引用在 read 时解析为环境变量值。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    storage = auth_storage_in_memory({})
    await storage.modify(
        "openai", lambda _c: _just(ApiKeyCredential(key="$OPENAI_API_KEY"))
    )

    cred = await storage.read("openai")
    assert cred is not None
    assert cred.type == "api_key"
    assert cred.key == "sk-from-env"


@pytest.mark.asyncio
async def test_credential_store_api_key_literal_passthrough():
    """裸字符串按字面量处理，不再隐式查找同名环境变量。"""
    storage = auth_storage_in_memory({})
    await storage.modify("openai", lambda _c: _just(ApiKeyCredential(key="sk-literal")))

    cred = await storage.read("openai")
    assert cred is not None
    assert cred.key == "sk-literal"


@pytest.mark.asyncio
async def test_credential_store_oauth_roundtrip():
    storage = auth_storage_in_memory({})
    cred = OAuthCredential(access="a", refresh="r", expires=1)
    await storage.modify("codex", lambda _current: _just(cred))

    read = await storage.read("codex")
    assert read is not None
    assert read.type == "oauth"
    assert read.access == "a"


@pytest.mark.asyncio
async def test_credential_store_list_and_delete():
    storage = auth_storage_in_memory({})
    await storage.modify("openai", lambda _current: _just(ApiKeyCredential(key="k1")))
    await storage.modify(
        "codex",
        lambda _current: _just(OAuthCredential(access="a", refresh="r", expires=1)),
    )

    infos = await storage.list()
    assert {info.provider_id for info in infos} == {"openai", "codex"}

    await storage.delete("openai")
    assert await storage.read("openai") is None
    assert await storage.read("codex") is not None


@pytest.mark.asyncio
async def test_credential_store_runtime_override_takes_priority_on_read():
    """对齐 TS RuntimeCredentials：runtime override 在 read/list 中优先于存储 credential。"""
    storage = auth_storage_in_memory({"openai": {"type": "api_key", "key": "stored"}})
    storage.set_runtime_api_key("openai", "runtime")

    cred = await storage.read("openai")
    assert cred is not None
    assert cred.key == "runtime"


@pytest.mark.asyncio
async def test_credential_store_list_includes_runtime_overrides():
    storage = auth_storage_in_memory({"openai": {"type": "api_key", "key": "stored"}})
    storage.set_runtime_api_key("custom-provider", "runtime")

    infos = await storage.list()
    by_provider = {info.provider_id: info for info in infos}
    assert by_provider["openai"].type == "api_key"
    assert by_provider["custom-provider"].type == "api_key"


@pytest.mark.asyncio
async def test_credential_store_delete_clears_runtime_override():
    storage = auth_storage_in_memory({})
    storage.set_runtime_api_key("openai", "runtime")

    await storage.delete("openai")
    assert await storage.read("openai") is None


# ---------------------------------------------------------------------------
# File persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_storage_persists_oauth_to_file(tmp_path):
    auth_path = tmp_path / "auth.json"
    storage = AuthStorage.create(auth_path)
    cred = OAuthCredential(
        access="access-token", refresh="refresh-token", expires=9999999999
    )
    await storage.modify("codex", lambda _current: _just(cred))

    # 重新从文件加载
    reloaded = AuthStorage.create(auth_path)
    read = await reloaded.read("codex")
    assert read is not None
    assert read.type == "oauth"
    assert read.access == "access-token"
    assert read.refresh == "refresh-token"


@pytest.mark.asyncio
async def test_auth_storage_oauth_refresh_updates_file(tmp_path):
    auth_path = tmp_path / "auth.json"
    storage = AuthStorage.create(auth_path)
    expired = OAuthCredential(access="old", refresh="r", expires=1)
    await storage.modify("codex", lambda _current: _just(expired))

    async def refresh(current):
        return OAuthCredential(
            access="new", refresh=current.refresh, expires=9999999999
        )

    await storage.modify("codex", refresh)

    reloaded = AuthStorage.create(auth_path)
    read = await reloaded.read("codex")
    assert read is not None
    assert read.access == "new"


async def _just(value):
    return value


@pytest.mark.asyncio
async def test_read_uses_credential_env_for_resolution(monkeypatch):
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
    cred = await storage.read("p")
    assert cred.key == "from-credential"


@pytest.mark.asyncio
async def test_read_unresolvable_key_becomes_none():
    """$VAR 解析失败时 key 置 None（下游回落 env 链），而非返回原文。"""
    storage = auth_storage_in_memory(
        {"p": {"type": "api_key", "key": "$NOVA_DEFINITELY_MISSING"}}
    )
    cred = await storage.read("p")
    assert cred is not None
    assert cred.key is None


@pytest.mark.asyncio
async def test_modify_rereads_file_inside_lock(tmp_path):
    """锁内重读：构造后由外部进程写入的 credential 不会被 modify 覆盖丢失。"""
    import json as _json

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(_json.dumps({}), encoding="utf-8")
    storage = AuthStorage.create(auth_path=auth_path)

    # 模拟另一个进程写入
    auth_path.write_text(
        _json.dumps({"other": {"type": "api_key", "key": "k-other"}}),
        encoding="utf-8",
    )

    async def _set(_current):
        return ApiKeyCredential(key="k-mine")

    await storage.modify("mine", _set)

    on_disk = _json.loads(auth_path.read_text(encoding="utf-8"))
    assert "other" in on_disk  # 外部写入未被覆盖
    assert on_disk["mine"]["key"] == "k-mine"


@pytest.mark.asyncio
async def test_delete_rereads_file_inside_lock(tmp_path):
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

    await storage.delete("a")
    on_disk = _json.loads(auth_path.read_text(encoding="utf-8"))
    assert "a" not in on_disk
    assert on_disk["b"]["key"] == "kb"


@pytest.mark.asyncio
async def test_delete_preserves_unparseable_entries(tmp_path):
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

    await storage.delete("mine")
    on_disk = _json.loads(auth_path.read_text(encoding="utf-8"))
    assert "mine" not in on_disk
    assert on_disk["victim"] == {"type": "apiKey", "key": "legacy-format"}  # 原样保留


@pytest.mark.asyncio
async def test_modify_preserves_unparseable_entries(tmp_path):
    """modify 写入同样保留无法解析的条目。"""
    import json as _json

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        _json.dumps({"victim": {"type": "apiKey", "key": "legacy"}}), encoding="utf-8"
    )
    storage = AuthStorage.create(auth_path=auth_path)

    from nova_ai.types.auth import ApiKeyCredential

    async def _set(_current):
        return ApiKeyCredential(type="api_key", key="new-key")

    await storage.modify("new-provider", _set)
    on_disk = _json.loads(auth_path.read_text(encoding="utf-8"))
    assert on_disk["new-provider"]["key"] == "new-key"
    assert on_disk["victim"] == {"type": "apiKey", "key": "legacy"}
