"""ModelsDevCatalog 测试（v2 切片/实例缓存去重/TTL）与翻译委托（v2 翻译件）。

翻译本体（价格分档/级别表推导/能力字段）的详尽用例归 nova_catalog 的
sources 测试；本文件只钉运行期适配层的行为承诺与"用的就是 v2 翻译件"
这一委托事实。
"""

import time

import pytest

from nova_ai.catalog_impls.models_dev import ModelsDevCatalog, models_dev_to_nova

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
        assert fields["id"] == "deepseek-v4"  # id 由切片键补回
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


class TestModelsDevToNovaDelegation:
    """运行期消费的就是构建管线的 v2 翻译件（过滤语义为证——v1 不过滤）。"""

    async def test_non_chat_type_dropped(self):
        assert models_dev_to_nova("p1", _v2_entry(type="image")) is None

    async def test_deprecated_kept_by_default_dropped_when_opted(self):
        """deprecated 缺省保留（对齐 pi modelsDevModels——只有个别 provider
        开启 skipDeprecated）；显式开启才剔除。"""
        entry = _v2_entry(status="deprecated")
        assert models_dev_to_nova("p1", entry) is not None
        assert models_dev_to_nova("p1", entry, skip_deprecated=True) is None

    async def test_tools_unsupported_dropped(self):
        entry = _v2_entry(capabilities={"tools": {"supported": False}})
        assert models_dev_to_nova("p1", entry) is None

    async def test_missing_name_falls_back_to_id(self):
        entry = _v2_entry()
        del entry["name"]
        fields = models_dev_to_nova("p1", entry)
        assert fields is not None
        assert fields["name"] == entry["id"]

    async def test_strict_raises_on_missing_id(self):
        entry = _v2_entry()
        del entry["id"]
        try:
            models_dev_to_nova("p1", entry, strict=True)
            raise AssertionError("should have raised")
        except ValueError as e:
            assert "missing id" in str(e)

    async def test_modalities_filtered_to_text_image(self):
        fields = models_dev_to_nova(
            "p1", _v2_entry(modalities={"input": ["text", "image", "audio"]})
        )
        assert fields is not None
        assert fields["input_types"] == ["text", "image"]

        only_audio = models_dev_to_nova(
            "p1", _v2_entry(modalities={"input": ["audio"]})
        )
        assert only_audio is not None
        assert only_audio["input_types"] == ["text"]  # 无有效模态回落 text
