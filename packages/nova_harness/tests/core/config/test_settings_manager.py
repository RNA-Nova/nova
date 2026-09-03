"""
SettingsManager 测试。
"""

from nova_ai import ModelThinkingLevel
from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.types.config.settings import ProviderRetrySettings, Settings
from tests._helpers.settings_manager import settings_manager_in_memory


def test_settings_manager_in_memory_defaults():
    sm = settings_manager_in_memory()
    assert sm.get_retry_enabled() is SettingsManager.DEFAULT_RETRY_ENABLED
    assert sm.get_compaction_enabled() is SettingsManager.DEFAULT_COMPACTION_ENABLED
    assert sm.get_steering_mode() == SettingsManager.DEFAULT_STEERING_MODE


def test_settings_manager_get_set_default_provider_model():
    sm = settings_manager_in_memory()
    sm.set_default_provider("volcengine")
    sm.set_default_model("deepseek-v4-flash-260425")
    assert sm.get_default_provider() == "volcengine"
    assert sm.get_default_model() == "deepseek-v4-flash-260425"


def test_settings_manager_thinking_level_roundtrip():
    sm = settings_manager_in_memory()
    sm.set_default_thinking_level(ModelThinkingLevel.HIGH)
    assert sm.get_default_thinking_level() == ModelThinkingLevel.HIGH
    sm.set_default_thinking_level(None)
    assert sm.get_default_thinking_level() is None


def test_settings_manager_nested_compaction_settings():
    sm = settings_manager_in_memory(
        {"compaction": {"enabled": False, "reserve_tokens": 8192}}
    )
    settings = sm.get_compaction_settings()
    assert settings.enabled is False
    assert settings.reserve_tokens == 8192


def test_settings_manager_set_compaction_enabled():
    sm = settings_manager_in_memory()
    sm.set_compaction_enabled(False)
    assert sm.get_compaction_enabled() is False


def test_settings_manager_project_override():
    sm = settings_manager_in_memory()
    sm.set_project_packages(["custom-source"])
    assert sm.get_packages() == ["custom-source"]


def test_settings_manager_apply_overrides():
    sm = settings_manager_in_memory()
    sm.apply_overrides(Settings(default_provider="volcengine"))
    assert sm.get_default_provider() == "volcengine"


def test_settings_manager_drain_errors():
    from nova_harness.core.types.config.settings import SettingsScope

    sm = settings_manager_in_memory()
    sm._record_error(SettingsScope.GLOBAL, ValueError("boom"))
    errors = sm.drain_errors()
    assert len(errors) == 1
    assert sm.drain_errors() == []


def test_unknown_fields_ignored_on_load():
    """历史/未知字段在加载时自然丢弃（pydantic 忽略未知键，无需迁移代码）。"""
    from tests._helpers.settings_storage import InMemorySettingsStorage

    storage = InMemorySettingsStorage()
    storage.with_lock(
        "global",
        lambda current: '{"queueMode": "all", "websockets": true, "steering_mode": "one-at-a-time"}',
    )
    sm = SettingsManager.from_storage(storage)
    # 未知键被忽略，已知键正常生效
    assert sm.get_steering_mode() == "one-at-a-time"


def test_settings_manager_default_project_trust_roundtrip():
    sm = settings_manager_in_memory()
    assert sm.get_default_project_trust() == "ask"
    sm.set_default_project_trust("always")
    assert sm.get_default_project_trust() == "always"
    sm.set_default_project_trust("never")
    assert sm.get_default_project_trust() == "never"


def test_settings_manager_retry_provider_defaults():
    sm = settings_manager_in_memory()
    settings = sm.get_retry_settings()
    assert settings.provider is not None
    assert (
        settings.provider.max_retry_delay_ms
        == SettingsManager.DEFAULT_RETRY_MAX_DELAY_MS
    )


def test_settings_manager_retry_provider_roundtrip():
    sm = settings_manager_in_memory()
    sm.set_retry_enabled(False)
    sm._global_settings.retry.provider = ProviderRetrySettings(max_retry_delay_ms=12345)
    sm._mark_modified("retry", "provider")
    sm._save()
    assert sm.get_retry_settings().provider.max_retry_delay_ms == 12345


def test_update_global_settings_partial_merge():
    """部分更新只影响显式键，未出现的字段保持现状。"""
    sm = settings_manager_in_memory()
    sm.set_steering_mode("all")
    result = sm.update_global_settings({"show_cache_miss_notices": True})
    assert sm.get_steering_mode() == "all"
    assert result.show_cache_miss_notices is True
    assert sm.get_settings().show_cache_miss_notices is True


def test_update_global_settings_rejects_unknown_key():
    """未知键显式拒绝（pydantic 默认 ignore extra，守卫防前端笔误静默）。"""
    sm = settings_manager_in_memory()
    try:
        sm.update_global_settings({"nope_key": 1})
    except ValueError as exc:
        assert "nope_key" in str(exc)
    else:
        raise AssertionError("unknown key should raise ValueError")


def test_update_global_settings_explicit_null_clears():
    """显式 null 清除已有值（对齐 deep_merge_settings 语义）。"""
    sm = settings_manager_in_memory()
    sm.set_default_provider("volcengine")
    assert sm.get_default_provider() == "volcengine"
    sm.update_global_settings({"default_provider": None})
    assert sm.get_default_provider() is None


def test_update_global_settings_validates_types():
    """类型错误走校验异常。"""
    sm = settings_manager_in_memory()
    try:
        sm.update_global_settings({"steering_mode": "sometimes"})
    except Exception:
        pass
    else:
        raise AssertionError("invalid literal should raise")


def test_register_executor_endpoint_creates_settings():
    """首次登记：executor 段不存在时自动创建。"""
    sm = settings_manager_in_memory()
    assert sm.get_executor_settings() is None
    sm.register_executor_endpoint("gpu-01", "ssh://liujinming@180.184.33.245")
    executor = sm.get_executor_settings()
    assert executor is not None
    assert executor.endpoints is not None
    assert [(e.name, e.url) for e in executor.endpoints] == [
        ("gpu-01", "ssh://liujinming@180.184.33.245")
    ]


def test_register_executor_endpoint_upsert_by_name():
    """同名覆盖（url 更新），异名追加。"""
    sm = settings_manager_in_memory()
    sm.register_executor_endpoint("gpu-01", "ssh://a@1.1.1.1")
    sm.register_executor_endpoint("gpu-02", "wss://gpu-02:8080")
    sm.register_executor_endpoint("gpu-01", "ssh://b@2.2.2.2")
    executor = sm.get_executor_settings()
    assert executor is not None and executor.endpoints is not None
    assert [(e.name, e.url) for e in executor.endpoints] == [
        ("gpu-01", "ssh://b@2.2.2.2"),
        ("gpu-02", "wss://gpu-02:8080"),
    ]


def test_unregister_executor_endpoint():
    """移除已登记端点；空清单回落 None；不存在返回 False。"""
    sm = settings_manager_in_memory()
    sm.register_executor_endpoint("gpu-01", "ssh://a@1.1.1.1")
    sm.register_executor_endpoint("gpu-02", "wss://gpu-02:8080")
    assert sm.unregister_executor_endpoint("gpu-01") is True
    executor = sm.get_executor_settings()
    assert executor is not None and executor.endpoints is not None
    assert [e.name for e in executor.endpoints] == ["gpu-02"]
    assert sm.unregister_executor_endpoint("gpu-02") is True
    assert sm.get_executor_settings().endpoints is None  # type: ignore[union-attr]
    assert sm.unregister_executor_endpoint("nope") is False


def test_register_executor_endpoint_preserves_default_backend():
    """登记端点不破坏已有 default_backend。"""
    sm = settings_manager_in_memory({"executor": {"default_backend": "executor"}})
    sm.register_executor_endpoint("gpu-01", "ssh://a@1.1.1.1")
    executor = sm.get_executor_settings()
    assert executor is not None
    assert executor.default_backend == "executor"
    assert executor.endpoints is not None and len(executor.endpoints) == 1


def test_register_executor_endpoint_with_cwd_roundtrip():
    """显式远程目录随端点记忆；重复登记可清除（None 覆盖）。"""
    sm = settings_manager_in_memory()
    sm.register_executor_endpoint("gpu-01", "ssh://a@1.1.1.1", cwd="/data/proj")
    executor = sm.get_executor_settings()
    assert executor is not None and executor.endpoints is not None
    assert executor.endpoints[0].cwd == "/data/proj"
    sm.register_executor_endpoint("gpu-01", "ssh://a@1.1.1.1")
    assert sm.get_executor_settings().endpoints[0].cwd is None  # type: ignore[index,union-attr]
