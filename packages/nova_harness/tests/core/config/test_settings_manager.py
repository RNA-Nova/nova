"""
SettingsManager 测试。
"""

from nova_ai import ThinkingLevel

from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.types.setting import Settings


def test_settings_manager_in_memory_defaults():
    sm = SettingsManager.in_memory()
    assert sm.get_retry_enabled() is SettingsManager.DEFAULT_RETRY_ENABLED
    assert sm.get_compaction_enabled() is SettingsManager.DEFAULT_COMPACTION_ENABLED
    assert sm.get_steering_mode() == SettingsManager.DEFAULT_STEERING_MODE


def test_settings_manager_get_set_default_provider_model():
    sm = SettingsManager.in_memory()
    sm.set_default_provider("volcengine")
    sm.set_default_model("deepseek-v3-2-251201")
    assert sm.get_default_provider() == "volcengine"
    assert sm.get_default_model() == "deepseek-v3-2-251201"


def test_settings_manager_thinking_level_roundtrip():
    sm = SettingsManager.in_memory()
    sm.set_default_thinking_level(ThinkingLevel.HIGH)
    assert sm.get_default_thinking_level() == ThinkingLevel.HIGH
    sm.set_default_thinking_level(None)
    assert sm.get_default_thinking_level() is None


def test_settings_manager_nested_compaction_settings():
    sm = SettingsManager.in_memory(
        {"compaction": {"enabled": False, "reserve_tokens": 8192}}
    )
    settings = sm.get_compaction_settings()
    assert settings.enabled is False
    assert settings.reserve_tokens == 8192


def test_settings_manager_set_compaction_enabled():
    sm = SettingsManager.in_memory()
    sm.set_compaction_enabled(False)
    assert sm.get_compaction_enabled() is False


def test_settings_manager_project_override():
    sm = SettingsManager.in_memory()
    sm.set_project_packages(["custom-source"])
    assert sm.get_packages() == ["custom-source"]


def test_settings_manager_apply_overrides():
    sm = SettingsManager.in_memory()
    sm.apply_overrides(Settings(theme="dark"))
    assert sm.get_theme() == "dark"


def test_settings_manager_drain_errors():
    from nova_harness.core.types.setting import SettingsScope

    sm = SettingsManager.in_memory()
    sm._record_error(SettingsScope.GLOBAL, ValueError("boom"))
    errors = sm.drain_errors()
    assert len(errors) == 1
    assert sm.drain_errors() == []
