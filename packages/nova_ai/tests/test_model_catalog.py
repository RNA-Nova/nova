"""模型目录引擎测试（model_catalog.py：修正/盖戳/拍平/生成时间锚点）。

知识替身直接用真类型 ``ProviderDefinition``——静态结构即契约，顺带
dogfood；修正引擎直引 ``nova_catalog.engine``（构建/运行同一台）。
"""

import pytest
from nova_catalog.define import ProviderDefinition
from nova_catalog.engine import apply_corrections
from nova_protocol import KnownApi

from nova_ai.model_catalog import (
    builtin_catalog_generated_at,
    finalize_catalog_models,
    flatten_model_catalog,
)


def _knowledge(**overrides):
    base = {
        "id": "test",
        "name": "Test",
        "base_url": "http://x/v1",
        "api": KnownApi.OPENAI_COMPLETIONS,
    }
    base.update(overrides)
    return ProviderDefinition(**base)


def _fields(model_id, **overrides):
    fields = {
        "id": model_id,
        "name": model_id,
        "base_url": "http://fields/v1",
        "api": "openai-completions",
        "reasoning": False,
        "input_types": ["text"],
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 128000,
        "max_tokens": 8192,
    }
    fields.update(overrides)
    return fields


class TestApplyCorrections:
    def test_none_corrections_passthrough(self):
        fields = _fields("m1")
        assert apply_corrections(None, "m1", fields) is fields
        assert "compat" not in fields

    def test_compat_defaults_merge_under_fields_compat(self):
        corrections = _knowledge(
            compat_defaults={"supports_store": False, "thinking_format": "deepseek"}
        )
        fields = _fields("m1", compat={"thinking_format": "openai"})
        result = apply_corrections(corrections, "m1", fields)
        # 默认垫底、字段包自带 compat 优先
        assert result["compat"] == {
            "supports_store": False,
            "thinking_format": "openai",
        }

    def test_model_corrections_glob_and_precedence(self):
        corrections = _knowledge(
            compat_defaults={"supports_store": False},
            model_corrections={
                "deepseek-*": {
                    "compat": {"thinking_format": "deepseek"},
                    "thinking_level_map": {"high": "high"},
                }
            },
        )
        hit = apply_corrections(corrections, "deepseek-v4", _fields("deepseek-v4"))
        # 模型级 compat 覆盖 provider 默认
        assert hit["compat"] == {
            "supports_store": False,
            "thinking_format": "deepseek",
        }
        assert hit["thinking_level_map"] == {"high": "high"}

        miss = apply_corrections(corrections, "other-m", _fields("other-m"))
        assert "thinking_level_map" not in miss

    def test_model_corrections_whitelist_keys(self):
        """修正键白名单：登记键（thinking_level_map/context_window/max_tokens）
        生效，白名单外键不散装赋值。"""
        corrections = _knowledge(
            model_corrections={
                "m1": {
                    "max_tokens": 32768,
                    "context_window": 65536,
                    "unknown_key": "x",
                }
            }
        )
        result = apply_corrections(corrections, "m1", _fields("m1"))
        assert result["max_tokens"] == 32768
        assert result["context_window"] == 65536
        assert "unknown_key" not in result

    def test_thinking_map_merges_per_key(self):
        """级别表按键合并：缺失键补，自带键保留，修正键逐键压。"""
        corrections = _knowledge(
            model_corrections={"m1": {"thinking_level_map": {"high": "high"}}}
        )
        # 缺失：补
        result = apply_corrections(corrections, "m1", _fields("m1"))
        assert result["thinking_level_map"] == {"high": "high"}
        # 自带（站/托管的活数据）：自带键保留 + 修正键并入
        live_map = {"max": "max"}
        result = apply_corrections(
            corrections, "m1", _fields("m1", thinking_level_map=live_map)
        )
        # 按键合并：自带 max 保留 + 修正 high 并入
        assert result["thinking_level_map"] == {"high": "high", "max": "max"}

    def test_inplace_contract(self):
        """原地修改入参并返回同一对象（调用方预拷贝纪律的锁定）。"""
        corrections = _knowledge(compat_defaults={"supports_store": False})
        fields = _fields("m1")
        result = apply_corrections(corrections, "m1", fields)
        assert result is fields
        assert fields["compat"] == {"supports_store": False}


class TestApiResolution:
    """api 盖戳解析（finalize 内联）：源字段 > 知识声明 > 响亮失败。"""

    def test_source_field_wins_over_declaration(self):
        fields = _fields("m1", api="anthropic-messages")
        models = finalize_catalog_models(
            "p1", "http://x/v1", [fields], knowledge=_knowledge()
        )
        assert models[0].api == "anthropic-messages"

    def test_declaration_fills_when_source_absent(self):
        fields = _fields("m1")
        del fields["api"]
        models = finalize_catalog_models(
            "p1",
            "http://x/v1",
            [fields],
            knowledge=_knowledge(api=KnownApi.ANTHROPIC_MESSAGES),
        )
        assert models[0].api == "anthropic-messages"

    def test_raise_when_neither(self):
        fields = _fields("m1")
        del fields["api"]
        with pytest.raises(ValueError, match="无法确定目录协议戳"):
            finalize_catalog_models("p1", "http://x/v1", [fields])


class TestFinalizeCatalogModels:
    def test_stamps_over_source_fields(self):
        """盖戳（provider/base_url）覆盖源字段里的同名键；api 随源。"""
        fields = _fields(
            "m1",
            base_url="http://source/v1",
            api="anthropic-messages",
            provider="other",
        )
        models = finalize_catalog_models("p1", "http://stamp/v1", [fields])
        assert len(models) == 1
        model = models[0]
        assert model.provider == "p1"
        assert model.base_url == "http://stamp/v1"
        assert model.api == "anthropic-messages"  # 源数据驱动（站上是啥就是啥）

    def test_dedup_by_id_last_wins(self):
        older = _fields("m1", name="old")
        newer = _fields("m1", name="new")
        models = finalize_catalog_models("p1", "http://x/v1", [older, newer])
        assert [m.name for m in models] == ["new"]

    def test_entries_without_id_skipped(self):
        fields = _fields("m1")
        del fields["id"]
        models = finalize_catalog_models("p1", "http://x/v1", [fields])
        assert models == []

    def test_pinned_merged_and_station_wins_on_conflict(self):
        corrections = _knowledge(
            pinned_models={
                "pinned-m": _fields("pinned-m"),
                "m1": _fields("m1", name="pinned"),
            }
        )
        models = finalize_catalog_models(
            "p1",
            "http://x/v1",
            [_fields("m1", name="station")],
            knowledge=corrections,
        )
        by_id = {m.id: m for m in models}
        assert set(by_id) == {"m1", "pinned-m"}
        assert by_id["m1"].name == "station"  # 同 id 站拉取胜

    def test_corrections_applied(self):
        corrections = _knowledge(
            model_corrections={"m1": {"thinking_level_map": {"high": "high"}}}
        )
        models = finalize_catalog_models(
            "p1", "http://x/v1", [_fields("m1")], knowledge=corrections
        )
        assert models[0].thinking_level_map == {"high": "high"}


class TestFlattenModelCatalog:
    def test_happy_path_stamps_api_and_provider(self):
        groups = {"openai-completions": {"m1": _fields("m1")}}
        models = flatten_model_catalog("p1", groups)
        assert models["m1"].api == "openai-completions"
        assert models["m1"].provider == "p1"

    def test_unknown_api_group_raises(self):
        with pytest.raises(ValueError, match="Unknown api group"):
            flatten_model_catalog("p1", {"no-such-api": {"m1": _fields("m1")}})

    def test_id_mismatch_raises(self):
        fields = _fields("other-id")
        with pytest.raises(ValueError, match="data id mismatch"):
            flatten_model_catalog("p1", {"openai-completions": {"m1": fields}})

    def test_api_mismatch_raises(self):
        fields = _fields("m1", api="anthropic-messages")
        with pytest.raises(ValueError, match="data api"):
            flatten_model_catalog("p1", {"openai-completions": {"m1": fields}})


class TestBuiltinCatalogGeneratedAt:
    def test_real_manifest_returns_epoch_ms(self):
        generated_at = builtin_catalog_generated_at()
        assert isinstance(generated_at, int)
        assert generated_at > 0

    def test_missing_manifest_returns_none(self, monkeypatch, tmp_path):
        # 缺清单路径：files() 锚到空目录，read_text 抛 FileNotFoundError 被吞 → None
        monkeypatch.setattr("importlib.resources.files", lambda anchor: tmp_path)
        assert builtin_catalog_generated_at() is None
