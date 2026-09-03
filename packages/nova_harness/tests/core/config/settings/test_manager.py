"""
SettingsManager 补充测试。

覆盖文件加载、持久化、reload、flush、错误处理以及大量 getter/setter。
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from nova_ai import ModelThinkingLevel
from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.config.settings.storage import FileSettingsStorage
from nova_harness.core.types.config.settings import (
    MarkdownSettings,
    Settings,
    SettingsError,
    SettingsScope,
)
from tests._helpers.settings_manager import settings_manager_in_memory
from tests._helpers.settings_storage import InMemorySettingsStorage


def test_create_from_storage_loads_global_and_project(tmp_path: Path):
    """from_storage 应同时加载 global 与 project 配置并合并。"""
    storage = FileSettingsStorage(
        cwd=str(tmp_path / "cwd"),
        agent_dir=str(tmp_path / "agent"),
    )
    # 预写 global 配置
    storage._global._ensure_parent()
    storage._global._path.write_text(
        json.dumps({"default_provider": "openai"}), encoding="utf-8"
    )
    # 预写 project 配置
    storage._project._ensure_parent()
    storage._project._path.write_text(
        json.dumps({"default_model": "gpt-4"}), encoding="utf-8"
    )

    sm = SettingsManager.from_storage(storage)

    assert sm.get_default_provider() == "openai"
    assert sm.get_default_model() == "gpt-4"


def test_from_storage_records_load_errors():
    """storage 加载异常时应记录到 errors 列表。"""
    sm = settings_manager_in_memory()
    sm._record_error(SettingsScope.GLOBAL, ValueError("global boom"))
    sm._record_error(SettingsScope.PROJECT, ValueError("project boom"))
    errors = sm.drain_errors()
    assert len(errors) == 2
    assert all(isinstance(e, SettingsError) for e in errors)


def test_in_memory_initial_settings():
    """in_memory 使用传入的初始设置。"""
    sm = settings_manager_in_memory({"steering_mode": "all"})
    assert sm.get_steering_mode() == "all"


def test_get_global_and_project_settings():
    """get_global_settings / get_project_settings 应返回深拷贝。"""
    sm = settings_manager_in_memory({"default_provider": "volcengine"})
    assert sm.get_global_settings().default_provider == "volcengine"
    assert sm.get_project_settings().default_provider is None

    original = sm.get_global_settings()
    original.default_provider = "mutated"
    assert sm.get_default_provider() == "volcengine"


def test_apply_overrides():
    """apply_overrides 应在当前合并配置上叠加额外值。"""
    sm = settings_manager_in_memory({"default_model": "m1"})
    sm.apply_overrides(Settings(default_provider="volcengine"))
    assert sm.get_default_model() == "m1"
    assert sm.get_default_provider() == "volcengine"


async def test_reload_clears_modified_and_merges(tmp_path: Path):
    """reload 应先 flush 待写修改，再从磁盘重新加载并清空修改标记。"""
    storage = FileSettingsStorage(
        cwd=str(tmp_path / "cwd"),
        agent_dir=str(tmp_path / "agent"),
    )
    storage._global._ensure_parent()
    storage._global._path.write_text(
        json.dumps({"default_provider": "volcengine"}), encoding="utf-8"
    )
    sm = SettingsManager.from_storage(storage)
    sm.set_default_provider("openai")
    await sm.flush()  # 确保内存修改已落盘
    assert sm.get_default_provider() == "openai"

    # 修改底层文件
    storage._global._path.write_text(
        json.dumps({"default_provider": "anthropic"}), encoding="utf-8"
    )
    await sm.reload()
    assert sm.get_default_provider() == "anthropic"


async def test_reload_keeps_error_when_load_fails():
    """reload 遇到异常时仍应保留 load error 状态。"""
    storage = InMemorySettingsStorage()
    sm = SettingsManager.from_storage(storage)
    storage._global._value = "bad"
    await sm.reload()
    assert sm._global_settings_load_error is not None


def test_flush_executes_writes_and_clears_queue(tmp_path: Path):
    """flush 应执行队列中的写任务。"""
    storage = FileSettingsStorage(
        cwd=str(tmp_path / "cwd"),
        agent_dir=str(tmp_path / "agent"),
    )
    sm = SettingsManager.from_storage(storage)
    sm.set_default_provider("openai")
    sm.flush_sync()

    content = json.loads(storage._global._path.read_text(encoding="utf-8"))
    assert content["default_provider"] == "openai"
    assert sm._write_queue.empty()


def test_flush_continues_after_task_error(capsys):
    """flush 遇到单个任务异常时应继续执行后续任务。"""
    sm = settings_manager_in_memory()
    failing_task = MagicMock(side_effect=RuntimeError("boom"))
    ok_task = MagicMock()
    sm._enqueue_write(failing_task)
    sm._enqueue_write(ok_task)
    sm.flush_sync()
    failing_task.assert_called_once()
    ok_task.assert_called_once()


@pytest.mark.asyncio
async def test_flush_async_executes_writes_and_clears_queue(tmp_path: Path):
    """async flush 应异步执行队列中的写任务并清空队列。"""
    storage = FileSettingsStorage(
        cwd=str(tmp_path / "cwd"),
        agent_dir=str(tmp_path / "agent"),
    )
    sm = SettingsManager.from_storage(storage)
    sm.set_default_provider("openai")
    await sm.flush()

    content = json.loads(storage._global._path.read_text(encoding="utf-8"))
    assert content["default_provider"] == "openai"
    assert sm._write_queue.empty()


def test_persist_nested_settings(tmp_path: Path):
    """嵌套设置（如 compaction）应只覆盖修改过的字段。"""
    storage = FileSettingsStorage(
        cwd=str(tmp_path / "cwd"),
        agent_dir=str(tmp_path / "agent"),
    )
    storage._global._ensure_parent()
    storage._global._path.write_text(
        json.dumps({"compaction": {"enabled": True, "reserve_tokens": 100}}),
        encoding="utf-8",
    )
    sm = SettingsManager.from_storage(storage)
    sm.set_compaction_enabled(False)
    sm.flush_sync()

    content = json.loads(storage._global._path.read_text(encoding="utf-8"))
    assert content["compaction"]["enabled"] is False
    assert content["compaction"]["reserve_tokens"] == 100


def test_save_skips_when_global_load_error(tmp_path: Path):
    """global 配置加载失败时，_save 不应写入 global。"""
    storage = InMemorySettingsStorage()
    sm = SettingsManager.from_storage(storage)
    sm._global_settings_load_error = ValueError("load failed")
    sm.set_default_provider("openai")
    sm.flush_sync()
    assert storage._global._value is None


def test_save_project_settings_skips_when_project_load_error():
    """project 配置加载失败时，_save_project_settings 不应写入 project。"""
    sm = settings_manager_in_memory()
    sm._project_settings_load_error = ValueError("load failed")
    sm.set_project_packages([])
    sm.flush_sync()
    assert sm._storage._project._value is None


class TestSettingsManagerGetterSetters:
    """批量覆盖简单 getter/setter。"""

    def test_default_provider_model(self):
        sm = settings_manager_in_memory()
        sm.set_default_model_and_provider("openai", "gpt-4")
        assert sm.get_default_provider() == "openai"
        assert sm.get_default_model() == "gpt-4"

    def test_steering_and_follow_up_modes(self):
        sm = settings_manager_in_memory()
        assert sm.get_steering_mode() == SettingsManager.DEFAULT_STEERING_MODE
        sm.set_steering_mode("all")
        assert sm.get_steering_mode() == "all"

        assert sm.get_follow_up_mode() == SettingsManager.DEFAULT_FOLLOW_UP_MODE
        sm.set_follow_up_mode("all")
        assert sm.get_follow_up_mode() == "all"

    def test_default_thinking_level(self):
        sm = settings_manager_in_memory()
        sm.set_default_thinking_level(ModelThinkingLevel.HIGH)
        assert sm.get_default_thinking_level() == ModelThinkingLevel.HIGH
        sm.set_default_thinking_level(None)
        assert sm.get_default_thinking_level() is None

    def test_compaction_defaults_and_setters(self):
        sm = settings_manager_in_memory()
        assert sm.get_compaction_enabled() is SettingsManager.DEFAULT_COMPACTION_ENABLED
        assert sm.get_compaction_reserve_tokens() == 16384
        assert sm.get_compaction_keep_recent_tokens() == 20000

        sm.set_compaction_enabled(False)
        assert sm.get_compaction_enabled() is False
        settings = sm.get_compaction_settings()
        assert settings.enabled is False

    def test_branch_summary_settings(self):
        sm = settings_manager_in_memory()
        assert sm.get_branch_summary_reserve_tokens() == 16384
        settings = sm.get_branch_summary_settings()
        assert settings.reserve_tokens == 16384

    def test_retry_settings(self):
        sm = settings_manager_in_memory()
        assert sm.get_retry_enabled() is True
        assert sm.get_retry_settings().max_retries == 3
        sm.set_retry_enabled(False)
        assert sm.get_retry_enabled() is False
        assert sm.get_retry_settings_raw().enabled is False

    def test_shell_path_and_prefix(self):
        sm = settings_manager_in_memory()
        sm.set_shell_path("/bin/zsh")
        assert sm.get_shell_path() == "/bin/zsh"
        sm.set_shell_command_prefix("[")
        assert sm.get_shell_command_prefix() == "["

    def test_packages(self):
        sm = settings_manager_in_memory()
        sm.set_packages(["pkg-a"])
        assert sm.get_packages() == ["pkg-a"]

    def test_extension_paths(self):
        sm = settings_manager_in_memory()
        sm.set_extension_paths(["/ext"])
        assert sm.get_extension_paths() == ["/ext"]

    def test_skill_paths(self):
        sm = settings_manager_in_memory()
        sm.set_skill_paths(["/skill"])
        assert sm.get_skill_paths() == ["/skill"]

    def test_prompt_template_paths(self):
        sm = settings_manager_in_memory()
        sm.set_prompt_template_paths(["/prompt"])
        assert sm.get_prompt_template_paths() == ["/prompt"]

    def test_enabled_models(self):
        sm = settings_manager_in_memory()
        sm.set_enabled_models(["gpt-*", "claude-*"])
        assert sm.get_enabled_models() == ["gpt-*", "claude-*"]

    def test_enable_skill_commands(self):
        sm = settings_manager_in_memory()
        assert sm.get_enable_skill_commands() is True
        sm.set_enable_skill_commands(False)
        # 显式设置为 False 后，getter 应返回 False
        assert sm.get_enable_skill_commands() is False
        sm.set_enable_skill_commands(True)
        assert sm.get_enable_skill_commands() is True

    def test_thinking_budgets(self):
        sm = settings_manager_in_memory({"thinking_budgets": {"low": 100}})
        budgets = sm.get_thinking_budgets()
        assert budgets is not None
        assert budgets.low == 100

    def test_image_auto_resize_and_block(self):
        sm = settings_manager_in_memory()
        assert sm.get_image_auto_resize() is True
        sm.set_image_auto_resize(False)
        assert sm.get_image_auto_resize() is False
        assert sm.get_block_images() is False
        sm.set_block_images(True)
        assert sm.get_block_images() is True

    def test_enabled_models(self):
        sm = settings_manager_in_memory()
        sm.set_enabled_models(["gpt-*", "claude-*"])
        assert sm.get_enabled_models() == ["gpt-*", "claude-*"]

    def test_theme_round_trip(self):
        """theme 是纯前端消费的 round-trip 字段：update 校验接受 + dump 透出。"""
        sm = settings_manager_in_memory()
        assert sm.get_settings().theme is None
        sm.update_global_settings({"theme": "light"})
        assert sm.get_settings().theme == "light"
        dumped = {
            k: v for k, v in sm.get_settings().model_dump().items() if v is not None
        }
        assert dumped["theme"] == "light"


class TestSettingsManagerProjectScope:
    """覆盖 project 级 setter。"""

    def test_project_packages(self):
        sm = settings_manager_in_memory()
        sm.set_project_packages([{"source": "path:./pkg"}])
        assert sm.get_packages() == [{"source": "path:./pkg"}]

    def test_project_extension_paths(self):
        sm = settings_manager_in_memory()
        sm.set_project_extension_paths(["./ext"])
        assert sm.get_extension_paths() == ["./ext"]

    def test_project_skill_paths(self):
        sm = settings_manager_in_memory()
        sm.set_project_skill_paths(["./skill"])
        assert sm.get_skill_paths() == ["./skill"]

    def test_project_prompt_template_paths(self):
        sm = settings_manager_in_memory()
        sm.set_project_prompt_template_paths(["./prompt"])
        assert sm.get_prompt_template_paths() == ["./prompt"]

    def test_project_packages(self):
        sm = settings_manager_in_memory()
        sm.set_project_packages([{"source": "path:./pkg"}])
        assert sm.get_packages() == [{"source": "path:./pkg"}]


def test_record_and_clear_modified_scope():
    """_record_error 与 _clear_modified_scope 应正常工作。"""
    sm = settings_manager_in_memory()
    sm._mark_modified("transport")
    sm._mark_project_modified("packages")
    sm._clear_modified_scope(SettingsScope.GLOBAL)
    assert sm._modified_fields == set()
    assert sm._modified_project_fields == {"packages"}


def test_setter_persists_automatically_via_background_worker(tmp_path: Path):
    """对齐 TS enqueueWrite：setter 入队后由后台线程自动串行落盘，
    不依赖 reload/flush 之外的隐式触发。"""
    storage = FileSettingsStorage(
        cwd=str(tmp_path / "cwd"),
        agent_dir=str(tmp_path / "agent"),
    )
    sm = SettingsManager.from_storage(storage)
    sm.set_default_provider("openai")
    sm.set_default_model("gpt-5")

    # 等待后台队列消费完（flush_sync 语义 = 等待队列清空）
    sm.flush_sync()

    content = json.loads(storage._global._path.read_text(encoding="utf-8"))
    assert content["default_provider"] == "openai"
    assert content["default_model"] == "gpt-5"


def test_write_queue_preserves_order(tmp_path: Path):
    """连续 setter 按序落盘，最终值以最后一次为准。"""
    storage = FileSettingsStorage(
        cwd=str(tmp_path / "cwd"),
        agent_dir=str(tmp_path / "agent"),
    )
    sm = SettingsManager.from_storage(storage)
    sm.set_default_provider("a")
    sm.set_default_provider("b")
    sm.set_default_provider("c")
    sm.flush_sync()

    content = json.loads(storage._global._path.read_text(encoding="utf-8"))
    assert content["default_provider"] == "c"


def test_deep_merge_explicit_null_clears_base():
    """显式 null 覆盖清除（对齐 TS 的 null 语义），未提供的字段保留。"""
    from nova_harness.core.config.settings.utils import deep_merge_settings

    base = Settings.model_validate(
        {"default_provider": "volcengine", "default_model": "m1"}
    )
    overrides = Settings.model_validate({"default_model": None})
    merged = deep_merge_settings(base, overrides)

    assert merged.default_provider == "volcengine"  # 未提供 → 保留
    assert merged.default_model is None  # 显式 null → 清除


def test_deep_merge_nested_fields_set():
    from nova_harness.core.config.settings.utils import deep_merge_settings

    base = Settings.model_validate(
        {"compaction": {"enabled": True, "reserve_tokens": 100}}
    )
    overrides = Settings.model_validate({"compaction": {"enabled": False}})
    merged = deep_merge_settings(base, overrides)

    assert merged.compaction.enabled is False
    assert merged.compaction.reserve_tokens == 100  # 嵌套未提供 → 保留
