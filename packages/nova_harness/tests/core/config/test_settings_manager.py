"""
SettingsManager 测试。
"""

from nova_ai import ThinkingLevel

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
    sm.set_default_model("deepseek-v3-2-251201")
    assert sm.get_default_provider() == "volcengine"
    assert sm.get_default_model() == "deepseek-v3-2-251201"


def test_settings_manager_thinking_level_roundtrip():
    sm = settings_manager_in_memory()
    sm.set_default_thinking_level(ThinkingLevel.HIGH)
    assert sm.get_default_thinking_level() == ThinkingLevel.HIGH
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
    sm.apply_overrides(Settings(transport="websocket"))
    # transport 已固定为 sse，外部写入被忽略。
    assert sm.get_transport() == "sse"


def test_settings_manager_drain_errors():
    from nova_harness.core.types.config.settings import SettingsScope

    sm = settings_manager_in_memory()
    sm._record_error(SettingsScope.GLOBAL, ValueError("boom"))
    errors = sm.drain_errors()
    assert len(errors) == 1
    assert sm.drain_errors() == []


def test_migrate_settings_queueMode_to_steeringMode():
    data = {"queueMode": "all"}
    migrated = SettingsManager.migrate_settings(data)
    assert migrated["steering_mode"] == "all"
    assert "queueMode" not in migrated


def test_migrate_settings_websockets_to_transport():
    # transport 已固定为 sse，旧 websockets=True 也迁移为 sse。
    data = {"websockets": True}
    migrated = SettingsManager.migrate_settings(data)
    assert migrated["transport"] == "sse"
    assert "websockets" not in migrated

    data = {"websockets": False}
    migrated = SettingsManager.migrate_settings(data)
    assert migrated["transport"] == "sse"


def test_migrate_settings_skills_object_to_array():
    data = {
        "skills": {
            "enableSkillCommands": False,
            "customDirectories": ["/path/to/skill"],
        }
    }
    migrated = SettingsManager.migrate_settings(data)
    assert migrated["skills"] == ["/path/to/skill"]
    assert migrated["enable_skill_commands"] is False


def test_migrate_settings_skills_object_without_dirs_removed():
    data = {"skills": {"enableSkillCommands": True}}
    migrated = SettingsManager.migrate_settings(data)
    assert "skills" not in migrated
    assert migrated["enable_skill_commands"] is True


def test_migrate_settings_retry_maxDelayMs_to_provider_max_retry_delay_ms():
    data = {"retry": {"maxDelayMs": 5000}}
    migrated = SettingsManager.migrate_settings(data)
    assert migrated["retry"]["provider"]["max_retry_delay_ms"] == 5000
    assert "maxDelayMs" not in migrated["retry"]
    assert "max_delay_ms" not in migrated["retry"]


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


def test_migrate_settings_applied_on_load():
    from tests._helpers.settings_storage import InMemorySettingsStorage

    storage = InMemorySettingsStorage()
    storage.with_lock("global", lambda current: '{"queueMode": "all"}')
    sm = SettingsManager.from_storage(storage)
    assert sm.get_steering_mode() == "all"
