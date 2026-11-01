"""store 接缝的契约套件（ModelsStore / CredentialStore——对位 codex
``file_cache_implements_models_cache_contract``）。

本模块不被 pytest 收集（不在 tests 目录、类名不带 ``Test`` 前缀）；
实例化方式见包 docstring（``nova_ai.testkit``）：子类继承契约类，
同名 fixture（``models_store`` / ``credential_store``）绑定实现工厂。
行为不许因实现而分裂。
"""

from nova_protocol import (
    ApiKeyCredential,
    Model,
    ModelCost,
    ModelsStoreEntry,
    OAuthCredential,
    RedactedString,
)


def _model(model_id: str) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider="p1",
        base_url="http://x/v1",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=8192,
    )


def _oauth(access: str = "tok") -> OAuthCredential:
    return OAuthCredential(
        access=RedactedString(access),
        refresh=RedactedString("r"),
        expires=9999999999999,
    )


class ModelsStoreContract:
    """ModelsStore 接缝契约（async——消费方在子类上加 ``@pytest.mark.asyncio``）。

    实例化处须提供 ``models_store`` fixture（返回被测实现实例）。
    """

    async def test_read_missing_returns_none(self, models_store):
        assert await models_store.read("nope") is None

    async def test_write_read_roundtrip(self, models_store):
        entry = ModelsStoreEntry(models=[_model("m1")], checked_at=123)
        await models_store.write("p1", entry)
        assert await models_store.read("p1") == entry  # 整体相等

    async def test_write_overwrites(self, models_store):
        first = ModelsStoreEntry(models=[_model("m1")], checked_at=1)
        second = ModelsStoreEntry(models=[_model("m2")], checked_at=2)
        await models_store.write("p1", first)
        await models_store.write("p1", second)
        assert await models_store.read("p1") == second

    async def test_delete_removes(self, models_store):
        await models_store.write("p1", ModelsStoreEntry(models=[], checked_at=1))
        await models_store.delete("p1")
        assert await models_store.read("p1") is None

    async def test_delete_missing_is_noop(self, models_store):
        await models_store.delete("nope")

    async def test_provider_ids_isolated(self, models_store):
        entry = ModelsStoreEntry(models=[_model("m1")], checked_at=1)
        await models_store.write("p1", entry)
        assert await models_store.read("p2") is None

    async def test_stored_entry_is_detached_from_caller_mutation(self, models_store):
        """写入后调用方改自己的 entry 对象不得影响已存内容（防共享引用）。"""
        entry = ModelsStoreEntry(models=[_model("m1")], checked_at=1)
        await models_store.write("p1", entry)
        entry.models.clear()
        stored = await models_store.read("p1")
        assert stored is not None
        assert [m.id for m in stored.models] == ["m1"]


class CredentialStoreContract:
    """CredentialStore 接缝契约（sync）。

    实例化处须提供 ``credential_store`` fixture（返回被测实现实例）。
    """

    def test_read_missing_returns_none(self, credential_store):
        assert credential_store.read("nope") is None

    def test_write_read_roundtrip(self, credential_store):
        credential_store.write("p1", _oauth())
        assert credential_store.read("p1") == _oauth()

    def test_write_overwrites(self, credential_store):
        credential_store.write("p1", _oauth("v1"))
        credential_store.write("p1", _oauth("v2"))
        assert credential_store.read("p1") == _oauth("v2")

    def test_delete_removes(self, credential_store):
        credential_store.write("p1", _oauth())
        credential_store.delete("p1")
        assert credential_store.read("p1") is None

    def test_delete_missing_is_noop(self, credential_store):
        credential_store.delete("nope")

    def test_provider_ids_isolated(self, credential_store):
        credential_store.write("p1", _oauth())
        assert credential_store.read("p2") is None

    def test_list_reports_metadata_without_secret(self, credential_store):
        credential_store.write("p1", ApiKeyCredential(key=RedactedString("sk-secret")))
        infos = credential_store.list()
        assert len(infos) == 1
        assert infos[0].provider_id == "p1"
        assert "sk-secret" not in repr(infos[0])


__all__ = ["CredentialStoreContract", "ModelsStoreContract"]
