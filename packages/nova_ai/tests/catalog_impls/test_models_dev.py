"""ModelsDevCatalog 测试（v2 切片/实例缓存去重/TTL）与翻译委托（v2 翻译件）。

翻译本体（价格分档/级别表推导/能力字段）的详尽用例归 nova_catalog 的
sources 测试；本文件只钉运行期适配层的行为承诺与"用的就是 v2 翻译件"
这一委托事实。
"""

import time

import pytest

from nova_ai.catalog_impls.models_dev import ModelsDevCatalog

pytestmark = pytest.mark.asyncio


def _v2_entry(**overrides):
    """合法 v2 条目基座（chat 类型 + 工具支持——v2 翻译件的入门门槛）。"""
    entry = {
        "id": "deepseek-v4",
        "name": "DeepSeek V4",
        "type": "chat",
        "capabilities": {
            "tools": {"supported": True},
            "reasoning": {"supported": True},
        },
        "limit": {"context": 1048576, "output": 393216},
        "cost": {"text": {"input": {"price": 1}, "output": {"price": 2}}},
        "modalities": {"input": ["text"]},
    }
    entry.update(overrides)
    return entry


def _models_dev_payload():
    return {"volcengine": {"models": {"deepseek-v4": _v2_entry()}}}


class TestModelsDevCatalog:
    async def test_slice_and_translate(self, monkeypatch):
        async def fake_fetch_json(url, **kwargs):
            return _models_dev_payload()

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        outcome = await ModelsDevCatalog().fetch("volcengine", None, None)
        assert outcome.status == 200
        assert len(outcome.models_fields) == 1
        fields = outcome.models_fields[0]
        assert fields["id"] == "deepseek-v4"  # id 由条目内 source.id 补回
        assert fields["context_window"] == 1048576
        assert fields["max_tokens"] == 393216
        assert fields["reasoning"] is True
        # v2 嵌套价格被翻译件拍平
        assert fields["cost"]["input"] == 1
        assert fields["cost"]["output"] == 2

    async def test_unknown_key_is_404(self, monkeypatch):
        async def fake_fetch_json(url, **kwargs):
            return _models_dev_payload()

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        outcome = await ModelsDevCatalog().fetch("nope", None, None)
        assert outcome.status == 404

    async def test_empty_models_is_200_not_missing_provider(self, monkeypatch):
        async def fake_fetch_json(url, **kwargs):
            return {"volcengine": {"models": {}}}

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        outcome = await ModelsDevCatalog().fetch("volcengine", None, None)
        assert outcome.status == 200
        assert outcome.models_fields == []

    async def test_source_status_is_preserved_for_materialize(self, monkeypatch):
        async def fake_fetch_json(url, **kwargs):
            return {
                "volcengine": {
                    "models": {"old": _v2_entry(id="old", status="deprecated")}
                }
            }

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        outcome = await ModelsDevCatalog().fetch("volcengine", None, None)
        assert outcome.status == 200
        assert len(outcome.models_fields) == 1
        assert outcome.source_status == {"old": "deprecated"}

    async def test_entry_id_wins_over_outer_dictionary_key(self, monkeypatch):
        async def fake_fetch_json(url, **kwargs):
            return {"volcengine": {"models": {"alias-key": _v2_entry(id="canonical")}}}

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        outcome = await ModelsDevCatalog().fetch("volcengine", None, None)
        assert outcome.models_fields[0]["id"] == "canonical"

    async def test_instance_cache_dedups_downloads(self, monkeypatch):
        calls = []

        async def fake_fetch_json(url, **kwargs):
            calls.append(url)
            return _models_dev_payload()

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        catalog = ModelsDevCatalog()
        await catalog.fetch("volcengine", None, None)
        await catalog.fetch("volcengine", None, None)
        assert len(calls) == 1  # 60s 实例缓存：第二次共享第一次的下载

    async def test_fetch_failure_is_status_0(self, monkeypatch):
        async def fake_fetch_json(url, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        outcome = await ModelsDevCatalog().fetch("volcengine", None, None)
        assert outcome.status == 0

    async def test_failure_not_cached_next_call_retries(self, monkeypatch):
        """失败不落缓存：下次调用真实重试（可成功）。"""
        calls = []

        async def fake_fetch_json(url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return _models_dev_payload()

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        catalog = ModelsDevCatalog()
        first = await catalog.fetch("volcengine", None, None)
        second = await catalog.fetch("volcengine", None, None)
        assert first.status == 0
        assert second.status == 200
        assert len(calls) == 2

    async def test_non_object_payload_is_status_0_and_not_cached(self, monkeypatch):
        calls = []

        async def fake_fetch_json(url, **kwargs):
            calls.append(url)
            return [] if len(calls) == 1 else _models_dev_payload()

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        catalog = ModelsDevCatalog()
        first = await catalog.fetch("volcengine", None, None)
        second = await catalog.fetch("volcengine", None, None)
        assert first.status == 0
        assert isinstance(first.cause, ValueError)
        assert second.status == 200
        assert len(calls) == 2

    async def test_cache_expires_after_ttl(self, monkeypatch):
        """60s 窗口后缓存失效，重新下载。"""
        now = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: now[0])
        calls = []

        async def fake_fetch_json(url, **kwargs):
            calls.append(url)
            return _models_dev_payload()

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        catalog = ModelsDevCatalog()
        await catalog.fetch("volcengine", None, None)
        now[0] += 30  # 窗口内：命中缓存
        await catalog.fetch("volcengine", None, None)
        now[0] += 61  # 超窗：重新下载
        await catalog.fetch("volcengine", None, None)
        assert len(calls) == 2
