"""ModelsDevCatalog 测试（切片/实例缓存去重/TTL）与 models.dev 方言翻译。"""

import time

import pytest

from nova_ai.catalog_impls.models_dev import ModelsDevCatalog, models_dev_to_nova

pytestmark = pytest.mark.asyncio


def _models_dev_payload():
    return {
        "volcengine": {
            "models": {
                "deepseek-v4": {
                    "id": "deepseek-v4",
                    "name": "DeepSeek V4",
                    "reasoning": True,
                    "limit": {"context": 1048576, "output": 393216},
                    "cost": {"input": 1, "output": 2},
                    "modalities": {"input": ["text"]},
                }
            }
        }
    }


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
        assert fields["id"] == "deepseek-v4"  # id 由切片键补回
        assert fields["context_window"] == 1048576
        assert fields["max_tokens"] == 393216
        assert fields["reasoning"] is True

    async def test_unknown_key_is_404(self, monkeypatch):
        async def fake_fetch_json(url, **kwargs):
            return _models_dev_payload()

        monkeypatch.setattr(
            "nova_ai.catalog_impls.models_dev.fetch_json", fake_fetch_json
        )
        outcome = await ModelsDevCatalog().fetch("nope", None, None)
        assert outcome.status == 404

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


class TestModelsDevToNova:
    async def test_strict_raises_on_missing_name(self):
        try:
            models_dev_to_nova("p1", {"id": "m1"}, strict=True)
            raise AssertionError("should have raised")
        except ValueError as e:
            assert "missing id/name" in str(e)

    async def test_interleaved_maps_reasoning_content_compat(self):
        fields = models_dev_to_nova(
            "p1",
            _models_dev_payload()["volcengine"]["models"]["deepseek-v4"]
            | {"interleaved": {"field": "reasoning_content"}},
        )
        assert fields is not None
        assert fields["compat"] == {
            "requires_reasoning_content_on_assistant_messages": True
        }

    async def test_modalities_filtered_to_text_image(self):
        fields = models_dev_to_nova(
            "p1",
            _models_dev_payload()["volcengine"]["models"]["deepseek-v4"]
            | {"modalities": {"input": ["text", "image", "audio"]}},
        )
        assert fields is not None
        assert fields["input_types"] == ["text", "image"]

        only_audio = models_dev_to_nova(
            "p1",
            _models_dev_payload()["volcengine"]["models"]["deepseek-v4"]
            | {"modalities": {"input": ["audio"]}},
        )
        assert only_audio is not None
        assert only_audio["input_types"] == ["text"]  # 无有效模态回落 text
