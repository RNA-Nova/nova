"""
Settings management module with file-based and in-memory storage backends.
"""

import json
import os
import threading
from copy import deepcopy
from typing import Any, Callable, Literal, Optional, Union

from nova_ai import Transport,ThinkingLevel
from ..config import get_agent_dir
from .types import (
    BranchSummarySettings,
    CompactionSettings,
    ComputexSettings,
    ImageSettings,
    PackageSource,
    RetrySettings,
    Settings,
    SettingsError,
    SettingsScope,
    TerminalSettings,
    ThinkingBudgetsSettings,
)
from .storage import FileSettingsStorage, InMemorySettingsStorage, SettingsStorage
from .utils import deep_merge_settings


class SettingsManager:
    """Manages application settings with global and project scopes."""

    # 默认常量定义
    DEFAULT_RETRY_ENABLED = True
    DEFAULT_RETRY_MAX_RETRIES = 3
    DEFAULT_RETRY_BASE_DELAY_MS = 2000
    DEFAULT_RETRY_MAX_DELAY_MS = 60000

    DEFAULT_COMPACTION_ENABLED = True
    DEFAULT_COMPACTION_RESERVE_TOKENS = 16384
    DEFAULT_COMPACTION_KEEP_RECENT_TOKENS = 1000

    DEFAULT_BRANCH_SUMMARY_RESERVE_TOKENS = 16384

    DEFAULT_TERMINAL_SHOW_IMAGES = True
    DEFAULT_IMAGE_AUTO_RESIZE = True
    DEFAULT_IMAGE_BLOCK_IMAGES = False

    DEFAULT_EDITOR_PADDING_X = 0
    DEFAULT_AUTOCOMPLETE_MAX_VISIBLE = 5
    DEFAULT_DOUBLE_ESCAPE_ACTION = "tree"
    DEFAULT_STEERING_MODE = "one-at-a-time"
    DEFAULT_FOLLOW_UP_MODE = "one-at-a-time"
    DEFAULT_CODE_BLOCK_INDENT = "  "

    DEFAULT_COMPUTEX_HOST = "127.0.0.1"
    DEFAUlt_COMPUtEX_PORT = 50001

    def __init__(
        self,
        storage: SettingsStorage,
        initial_global: Settings,
        initial_project: Settings,
        global_load_error: Optional[Exception] = None,
        project_load_error: Optional[Exception] = None,
        initial_errors: Optional[list[SettingsError]] = None
    ) -> None:
        self._storage = storage
        self._global_settings = initial_global
        self._project_settings = initial_project
        self._global_settings_load_error = global_load_error
        self._project_settings_load_error = project_load_error
        self._errors = initial_errors or []

        self._settings = deep_merge_settings(self._global_settings, self._project_settings)
        self._modified_fields: set[str] = set()
        self._modified_nested_fields: dict[str, set[str]] = {}
        self._modified_project_fields: set[str] = set()
        self._modified_project_nested_fields: dict[str, set[str]] = {}
        self._write_queue: list[Callable[[], None]] = []
        self._write_lock = threading.Lock()

    @classmethod
    def create(
        cls,
        cwd: str = os.getcwd(),
        agent_dir: str = get_agent_dir()
    ) -> "SettingsManager":
        """Create a SettingsManager that loads from files."""
        storage = FileSettingsStorage(cwd, agent_dir)
        return cls.from_storage(storage)

    @classmethod
    def from_storage(cls, storage: SettingsStorage) -> "SettingsManager":
        """Create a SettingsManager from an arbitrary storage backend."""
        global_load = cls._try_load_from_storage(storage, SettingsScope.GLOBAL)
        project_load = cls._try_load_from_storage(storage, SettingsScope.PROJECT)

        initial_errors: list[SettingsError] = []
        if global_load["error"]:
            initial_errors.append(SettingsError(SettingsScope.GLOBAL, global_load["error"]))
        if project_load["error"]:
            initial_errors.append(SettingsError(SettingsScope.PROJECT, project_load["error"]))

        return cls(
            storage,
            global_load["settings"],
            project_load["settings"],
            global_load["error"],
            project_load["error"],
            initial_errors
        )

    @classmethod
    def in_memory(cls, settings: Optional[dict[str, Any]] = None) -> "SettingsManager":
        """Create an in-memory SettingsManager (no file I/O)."""
        storage = InMemorySettingsStorage()
        initial = Settings.from_dict(settings) if settings else Settings()
        return cls(storage, initial, Settings())

    @staticmethod
    def _load_from_storage(
        storage: SettingsStorage,
        scope: SettingsScope
    ) -> Settings:
        """Load settings from storage."""
        content: Optional[str] = None

        def getter(current: Optional[str]) -> Optional[str]:
            nonlocal content
            content = current
            return None

        storage.with_lock(scope, getter)

        if not content:
            return Settings()

        data = json.loads(content)
        return Settings.from_dict(data)

    @classmethod
    def _try_load_from_storage(
        cls,
        storage: SettingsStorage,
        scope: SettingsScope
    ) -> dict[str, Any]:
        """Try to load settings, catching errors."""
        try:
            return {"settings": cls._load_from_storage(storage, scope), "error": None}
        except Exception as e:
            return {"settings": Settings(), "error": e}

    def get_global_settings(self) -> Settings:
        """Return a copy of global settings."""
        return deepcopy(self._global_settings)

    def get_project_settings(self) -> Settings:
        """Return a copy of project settings."""
        return deepcopy(self._project_settings)

    def reload(self) -> None:
        """Reload settings from storage."""
        global_load = self._try_load_from_storage(self._storage, SettingsScope.GLOBAL)
        if not global_load["error"]:
            self._global_settings = global_load["settings"]
            self._global_settings_load_error = None
        else:
            self._global_settings_load_error = global_load["error"]
            self._record_error(SettingsScope.GLOBAL, global_load["error"])

        self._modified_fields.clear()
        self._modified_nested_fields.clear()
        self._modified_project_fields.clear()
        self._modified_project_nested_fields.clear()

        project_load = self._try_load_from_storage(self._storage, SettingsScope.PROJECT)
        if not project_load["error"]:
            self._project_settings = project_load["settings"]
            self._project_settings_load_error = None
        else:
            self._project_settings_load_error = project_load["error"]
            self._record_error(SettingsScope.PROJECT, project_load["error"])

        self._settings = deep_merge_settings(self._global_settings, self._project_settings)

    def apply_overrides(self, overrides: Settings) -> None:
        """Apply additional overrides on top of current settings."""
        self._settings = deep_merge_settings(self._settings, overrides)

    def _mark_modified(self, field: str, nested_key: Optional[str] = None) -> None:
        """Mark a global field as modified during this session."""
        self._modified_fields.add(field)
        if nested_key:
            if field not in self._modified_nested_fields:
                self._modified_nested_fields[field] = set()
            self._modified_nested_fields[field].add(nested_key)

    def _mark_project_modified(self, field: str, nested_key: Optional[str] = None) -> None:
        """Mark a project field as modified during this session."""
        self._modified_project_fields.add(field)
        if nested_key:
            if field not in self._modified_project_nested_fields:
                self._modified_project_nested_fields[field] = set()
            self._modified_project_nested_fields[field].add(nested_key)

    def _record_error(self, scope: SettingsScope, error: Exception) -> None:
        """Record an error."""
        self._errors.append(SettingsError(scope, error))

    def _clear_modified_scope(self, scope: SettingsScope) -> None:
        """Clear modified tracking for a scope."""
        if scope == SettingsScope.GLOBAL:
            self._modified_fields.clear()
            self._modified_nested_fields.clear()
        else:
            self._modified_project_fields.clear()
            self._modified_project_nested_fields.clear()

    def _enqueue_write(self, scope: SettingsScope, task: Callable[[], None]) -> None:
        """Enqueue a write task."""
        with self._write_lock:
            self._write_queue.append(task)

    def _clone_modified_nested(
        self,
        source: dict[str, set[str]]
    ) -> dict[str, set[str]]:
        """Clone the modified nested fields tracking."""
        return {k: set(v) for k, v in source.items()}

    def _persist_scoped_settings(
        self,
        scope: SettingsScope,
        snapshot_settings: Settings,
        modified_fields: set[str],
        modified_nested: dict[str, set[str]]
    ) -> None:
        """Persist settings for a specific scope."""

        def writer(current: Optional[str]) -> Optional[str]:
            current_file_settings = {}
            if current:
                current_file_settings = json.loads(current)
                
            merged_settings = dict(current_file_settings)

            for field in modified_fields:
                value = getattr(snapshot_settings, field)
                if field in modified_nested and value is not None and hasattr(value, '__dataclass_fields__'):
                    nested_modified = modified_nested[field]
                    base_nested = current_file_settings.get(field, {})
                    if isinstance(base_nested, dict):
                        merged_nested = dict(base_nested)
                        for nested_key in nested_modified:
                            if hasattr(value, nested_key):
                                # 使用 mashumaro 将嵌套 dataclass 转为 dict
                                nested_val = getattr(value, nested_key)
                                if nested_val is not None and hasattr(nested_val, '__dataclass_fields__'):
                                    merged_nested[nested_key] = nested_val.to_dict()
                                else:
                                    merged_nested[nested_key] = nested_val
                        merged_settings[field] = merged_nested
                    else:
                        merged_settings[field] = value.to_dict() if hasattr(value, 'to_dict') else value
                else:
                    # 使用 mashumaro 的 to_dict() 方法
                    if value is not None and hasattr(value, 'to_dict'):
                        merged_settings[field] = value.to_dict()
                    else:
                        merged_settings[field] = value

            return json.dumps(merged_settings, indent=2, ensure_ascii=False)

        self._storage.with_lock(scope, writer)

    def _save(self) -> None:
        """Save global settings."""
        self._settings = deep_merge_settings(self._global_settings, self._project_settings)

        if self._global_settings_load_error:
            return

        snapshot_global = deepcopy(self._global_settings)
        modified_fields = set(self._modified_fields)
        modified_nested = self._clone_modified_nested(self._modified_nested_fields)

        def task() -> None:
            self._persist_scoped_settings(
                SettingsScope.GLOBAL,
                snapshot_global,
                modified_fields,
                modified_nested
            )
            self._clear_modified_scope(SettingsScope.GLOBAL)

        self._enqueue_write(SettingsScope.GLOBAL, task)

    def _save_project_settings(self, settings: Settings) -> None:
        """Save project settings."""
        self._project_settings = deepcopy(settings)
        self._settings = deep_merge_settings(self._global_settings, self._project_settings)

        if self._project_settings_load_error:
            return

        snapshot_project = deepcopy(self._project_settings)
        modified_fields = set(self._modified_project_fields)
        modified_nested = self._clone_modified_nested(self._modified_project_nested_fields)

        def task() -> None:
            self._persist_scoped_settings(
                SettingsScope.PROJECT,
                snapshot_project,
                modified_fields,
                modified_nested
            )
            self._clear_modified_scope(SettingsScope.PROJECT)

        self._enqueue_write(SettingsScope.PROJECT, task)

    def flush(self) -> None:
        """Execute all pending write operations."""
        with self._write_lock:
            for task in self._write_queue:
                try:
                    task()
                except Exception as e:
                    # Log error but continue with other tasks
                    print(f"Error flushing settings: {e}")
            self._write_queue.clear()

    def drain_errors(self) -> list[SettingsError]:
        """Drain and return all recorded errors."""
        drained = self._errors.copy()
        self._errors.clear()
        return drained

    # Getter and setter methods (保持不变，但使用 mashumaro 进行序列化)

    def get_last_changelog_version(self) -> Optional[str]:
        return self._settings.last_changelog_version

    def set_last_changelog_version(self, version: str) -> None:
        self._global_settings.last_changelog_version = version
        self._mark_modified("last_changelog_version")
        self._save()

    def get_default_provider(self) -> Optional[str]:
        return self._settings.default_provider

    def get_default_model(self) -> Optional[str]:
        return self._settings.default_model

    def set_default_provider(self, provider: str) -> None:
        self._global_settings.default_provider = provider
        self._mark_modified("default_provider")
        self._save()

    def set_default_model(self, model_id: str) -> None:
        self._global_settings.default_model = model_id
        self._mark_modified("default_model")
        self._save()

    def set_default_model_and_provider(self, provider: str, model_id: str) -> None:
        self._global_settings.default_provider = provider
        self._global_settings.default_model = model_id
        self._mark_modified("default_provider")
        self._mark_modified("default_model")
        self._save()

    def get_steering_mode(self) -> Literal["all", "one-at-a-time"]:
        return self._settings.steering_mode or self.DEFAULT_STEERING_MODE

    def set_steering_mode(self, mode: Literal["all", "one-at-a-time"]) -> None:
        self._global_settings.steering_mode = mode
        self._mark_modified("steering_mode")
        self._save()

    def get_follow_up_mode(self) -> Literal["all", "one-at-a-time"]:
        return self._settings.follow_up_mode or self.DEFAULT_FOLLOW_UP_MODE

    def set_follow_up_mode(self, mode: Literal["all", "one-at-a-time"]) -> None:
        self._global_settings.follow_up_mode = mode
        self._mark_modified("follow_up_mode")
        self._save()

    def get_theme(self) -> Optional[str]:
        return self._settings.theme

    def set_theme(self, theme: str) -> None:
        self._global_settings.theme = theme
        self._mark_modified("theme")
        self._save()

    def get_default_thinking_level(
        self
    ) -> Optional[ThinkingLevel]:
        return self._settings.default_thinking_level

    def set_default_thinking_level(
        self,
        level: Optional[ThinkingLevel] = None
    ) -> None:
        self._global_settings.default_thinking_level = level
        self._mark_modified("default_thinking_level")
        self._save()

    def get_transport(self) -> "Transport":
        return self._settings.transport or "sse"

    def set_transport(self, transport: "Transport") -> None:
        self._global_settings.transport = transport
        self._mark_modified("transport")
        self._save()

    def get_compaction_enabled(self) -> bool:
        if self._settings.compaction is not None and self._settings.compaction.enabled is not None:
            return self._settings.compaction.enabled
        return self.DEFAULT_COMPACTION_ENABLED

    def set_compaction_enabled(self, enabled: bool) -> None:
        if self._global_settings.compaction is None:
            self._global_settings.compaction = CompactionSettings()
        self._global_settings.compaction.enabled = enabled
        self._mark_modified("compaction", "enabled")
        self._save()

    def get_compaction_reserve_tokens(self) -> int:
        if self._settings.compaction is not None and self._settings.compaction.reserve_tokens is not None:
            return self._settings.compaction.reserve_tokens
        return self.DEFAULT_COMPACTION_RESERVE_TOKENS

    def get_compaction_keep_recent_tokens(self) -> int:
        if self._settings.compaction is not None and self._settings.compaction.keep_recent_tokens is not None:
            return self._settings.compaction.keep_recent_tokens
        return self.DEFAULT_COMPACTION_KEEP_RECENT_TOKENS

    def get_compaction_settings(self) -> CompactionSettings:
        """获取 compaction 设置，None 值会被替换为默认值。"""
        return CompactionSettings.from_dict({
            "enabled": self.get_compaction_enabled(),
            "reserve_tokens": self.get_compaction_reserve_tokens(),
            "keep_recent_tokens": self.get_compaction_keep_recent_tokens()
        })

    def get_branch_summary_settings(self) -> BranchSummarySettings:
        """获取 branch summary 设置，保留 None 值。"""
        return BranchSummarySettings(
            reserve_tokens=self.get_branch_summary_reserve_tokens()
        )
    def get_branch_summary_reserve_tokens(self) -> int:
        """获取 branch summary 的 reserve_tokens，使用默认值。"""
        if self._settings.branch_summary is not None and self._settings.branch_summary.reserve_tokens is not None:
            return self._settings.branch_summary.reserve_tokens
        return self.DEFAULT_BRANCH_SUMMARY_RESERVE_TOKENS

    def get_retry_enabled(self) -> bool:
        if self._settings.retry is not None and self._settings.retry.enabled is not None:
            return self._settings.retry.enabled
        return self.DEFAULT_RETRY_ENABLED

    def set_retry_enabled(self, enabled: bool) -> None:
        if self._global_settings.retry is None:
            self._global_settings.retry = RetrySettings()
        self._global_settings.retry.enabled = enabled
        self._mark_modified("retry", "enabled")
        self._save()

    def get_retry_settings(self) -> RetrySettings:
        """获取 retry 设置，所有 None 值会被替换为默认值。"""
        retry = self._settings.retry
        return RetrySettings.from_dict({
            "enabled": self.get_retry_enabled(),
            "max_retries": retry.max_retries if retry is not None and retry.max_retries is not None else self.DEFAULT_RETRY_MAX_RETRIES,
            "base_delay_ms": retry.base_delay_ms if retry is not None and retry.base_delay_ms is not None else self.DEFAULT_RETRY_BASE_DELAY_MS,
            "max_delay_ms": retry.max_delay_ms if retry is not None and retry.max_delay_ms is not None else self.DEFAULT_RETRY_MAX_DELAY_MS
        })

    def get_retry_settings_raw(self) -> Optional[RetrySettings]:
        """获取原始的 RetrySettings 对象，可能包含 None 字段。"""
        return self._settings.retry

    def get_hide_thinking_block(self) -> bool:
        return self._settings.hide_thinking_block or False

    def set_hide_thinking_block(self, hide: bool) -> None:
        self._global_settings.hide_thinking_block = hide
        self._mark_modified("hide_thinking_block")
        self._save()

    def get_shell_path(self) -> Optional[str]:
        return self._settings.shell_path

    def set_shell_path(self, path: Optional[str]) -> None:
        self._global_settings.shell_path = path
        self._mark_modified("shell_path")
        self._save()

    def get_quiet_startup(self) -> bool:
        return self._settings.quiet_startup or False

    def set_quiet_startup(self, quiet: bool) -> None:
        self._global_settings.quiet_startup = quiet
        self._mark_modified("quiet_startup")
        self._save()

    def get_shell_command_prefix(self) -> Optional[str]:
        return self._settings.shell_command_prefix

    def set_shell_command_prefix(self, prefix: Optional[str]) -> None:
        self._global_settings.shell_command_prefix = prefix
        self._mark_modified("shell_command_prefix")
        self._save()

    def get_collapse_changelog(self) -> bool:
        return self._settings.collapse_changelog or False

    def set_collapse_changelog(self, collapse: bool) -> None:
        self._global_settings.collapse_changelog = collapse
        self._mark_modified("collapse_changelog")
        self._save()

    def get_packages(self) -> list[PackageSource]:
        return list(self._settings.packages) if self._settings.packages is not None else []

    def set_packages(self, packages: list[PackageSource]) -> None:
        self._global_settings.packages = packages
        self._mark_modified("packages")
        self._save()

    def set_project_packages(self, packages: list[PackageSource]) -> None:
        project_settings = deepcopy(self._project_settings)
        project_settings.packages = packages
        self._mark_project_modified("packages")
        self._save_project_settings(project_settings)

    def get_extension_paths(self) -> list[str]:
        return list(self._settings.extensions) if self._settings.extensions is not None else []

    def set_extension_paths(self, paths: list[str]) -> None:
        self._global_settings.extensions = paths
        self._mark_modified("extensions")
        self._save()

    def set_project_extension_paths(self, paths: list[str]) -> None:
        project_settings = deepcopy(self._project_settings)
        project_settings.extensions = paths
        self._mark_project_modified("extensions")
        self._save_project_settings(project_settings)

    def get_skill_paths(self) -> list[str]:
        return list(self._settings.skills) if self._settings.skills is not None else []

    def set_skill_paths(self, paths: list[str]) -> None:
        self._global_settings.skills = paths
        self._mark_modified("skills")
        self._save()

    def set_project_skill_paths(self, paths: list[str]) -> None:
        project_settings = deepcopy(self._project_settings)
        project_settings.skills = paths
        self._mark_project_modified("skills")
        self._save_project_settings(project_settings)

    def get_prompt_template_paths(self) -> list[str]:
        return list(self._settings.prompts) if self._settings.prompts is not None else []

    def set_prompt_template_paths(self, paths: list[str]) -> None:
        self._global_settings.prompts = paths
        self._mark_modified("prompts")
        self._save()

    def set_project_prompt_template_paths(self, paths: list[str]) -> None:
        project_settings = deepcopy(self._project_settings)
        project_settings.prompts = paths
        self._mark_project_modified("prompts")
        self._save_project_settings(project_settings)

    def get_theme_paths(self) -> list[str]:
        return list(self._settings.themes) if self._settings.themes is not None else []

    def set_theme_paths(self, paths: list[str]) -> None:
        self._global_settings.themes = paths
        self._mark_modified("themes")
        self._save()

    def set_project_theme_paths(self, paths: list[str]) -> None:
        project_settings = deepcopy(self._project_settings)
        project_settings.themes = paths
        self._mark_project_modified("themes")
        self._save_project_settings(project_settings)

    def get_enable_skill_commands(self) -> bool:
        return self._settings.enable_skill_commands or True

    def set_enable_skill_commands(self, enabled: bool) -> None:
        self._global_settings.enable_skill_commands = enabled
        self._mark_modified("enable_skill_commands")
        self._save()

    def get_thinking_budgets(self) -> Optional[ThinkingBudgetsSettings]:
        return self._settings.thinking_budgets

    def get_show_images(self) -> bool:
        if self._settings.terminal is not None and self._settings.terminal.show_images is not None:
            return self._settings.terminal.show_images
        return self.DEFAULT_TERMINAL_SHOW_IMAGES

    def set_show_images(self, show: bool) -> None:
        if self._global_settings.terminal is None:
            self._global_settings.terminal = TerminalSettings()
        self._global_settings.terminal.show_images = show
        self._mark_modified("terminal", "show_images")
        self._save()

    def get_clear_on_shrink(self) -> bool:
        if self._settings.terminal is not None and self._settings.terminal.clear_on_shrink is not None:
            return self._settings.terminal.clear_on_shrink
        return os.environ.get("PI_CLEAR_ON_SHRINK") == "1"

    def set_clear_on_shrink(self, enabled: bool) -> None:
        if self._global_settings.terminal is None:
            self._global_settings.terminal = TerminalSettings()
        self._global_settings.terminal.clear_on_shrink = enabled
        self._mark_modified("terminal", "clear_on_shrink")
        self._save()

    def get_image_auto_resize(self) -> bool:
        if self._settings.images is not None and self._settings.images.auto_resize is not None:
            return self._settings.images.auto_resize
        return self.DEFAULT_IMAGE_AUTO_RESIZE

    def set_image_auto_resize(self, enabled: bool) -> None:
        if self._global_settings.images is None:
            self._global_settings.images = ImageSettings()
        self._global_settings.images.auto_resize = enabled
        self._mark_modified("images", "auto_resize")
        self._save()

    def get_block_images(self) -> bool:
        if self._settings.images is not None and self._settings.images.block_images is not None:
            return self._settings.images.block_images
        return self.DEFAULT_IMAGE_BLOCK_IMAGES

    def set_block_images(self, blocked: bool) -> None:
        if self._global_settings.images is None:
            self._global_settings.images = ImageSettings()
        self._global_settings.images.block_images = blocked
        self._mark_modified("images", "block_images")
        self._save()

    def get_enabled_models(self) -> Optional[list[str]]:
        return self._settings.enabled_models

    def set_enabled_models(self, patterns: Optional[list[str]]) -> None:
        self._global_settings.enabled_models = patterns
        self._mark_modified("enabled_models")
        self._save()

    def get_double_escape_action(self) -> Literal["fork", "tree", "none"]:
        return self._settings.double_escape_action or self.DEFAULT_DOUBLE_ESCAPE_ACTION

    def set_double_escape_action(self, action: Literal["fork", "tree", "none"]) -> None:
        self._global_settings.double_escape_action = action
        self._mark_modified("double_escape_action")
        self._save()

    def get_show_hardware_cursor(self) -> bool:
        if self._settings.show_hardware_cursor is not None:
            return self._settings.show_hardware_cursor
        return os.environ.get("PI_HARDWARE_CURSOR") == "1"

    def set_show_hardware_cursor(self, enabled: bool) -> None:
        self._global_settings.show_hardware_cursor = enabled
        self._mark_modified("show_hardware_cursor")
        self._save()

    def get_editor_padding_x(self) -> int:
        if self._settings.editor_padding_x is not None:
            return self._settings.editor_padding_x
        return self.DEFAULT_EDITOR_PADDING_X

    def set_editor_padding_x(self, padding: int) -> None:
        self._global_settings.editor_padding_x = max(0, min(3, int(padding)))
        self._mark_modified("editor_padding_x")
        self._save()

    def get_autocomplete_max_visible(self) -> int:
        if self._settings.autocomplete_max_visible is not None:
            return self._settings.autocomplete_max_visible
        return self.DEFAULT_AUTOCOMPLETE_MAX_VISIBLE

    def set_autocomplete_max_visible(self, max_visible: int) -> None:
        self._global_settings.autocomplete_max_visible = max(3, min(20, int(max_visible)))
        self._mark_modified("autocomplete_max_visible")
        self._save()

    def get_code_block_indent(self) -> str:
        if self._settings.markdown is not None and self._settings.markdown.code_block_indent is not None:
            return self._settings.markdown.code_block_indent
        return self.DEFAULT_CODE_BLOCK_INDENT
    
    def get_computex_host(self) -> str:
        """获取 Computex 主机地址。"""
        if self._settings.computex is not None and self._settings.computex.host is not None:
            return self._settings.computex.host
        return self.DEFAULT_COMPUTEX_HOST

    def set_computex_host(self, host: str) -> None:
        """设置 Computex 主机地址。"""
        if self._global_settings.computex is None:
            self._global_settings.computex = ComputexSettings()
        self._global_settings.computex.host = host
        self._mark_modified("computex", "host")
        self._save()

    def set_project_computex_host(self, host: str) -> None:
        project_settings = deepcopy(self._project_settings)
        if project_settings.computex is None:
            project_settings.computex = ComputexSettings()
        project_settings.computex.host = host
        self._mark_project_modified("computex", "host")
        self._save_project_settings(project_settings)

    def get_computex_port(self) -> int:
        """获取 Computex 端口号。"""
        if self._settings.computex is not None and self._settings.computex.port is not None:
            return self._settings.computex.port
        return self.DEFAULT_COMPUTEX_PORT

    def set_computex_port(self, port: int) -> None:
        """设置 Computex 端口号。"""
        if self._global_settings.computex is None:
            self._global_settings.computex = ComputexSettings()
        self._global_settings.computex.port = port
        self._mark_modified("computex", "port")
        self._save()
    
    def set_project_computex_port(self, port: int) -> None:
        project_settings = deepcopy(self._project_settings)
        if project_settings.computex is None:
            project_settings.computex = ComputexSettings()
        project_settings.computex.port = port
        self._mark_project_modified("computex", "port")
        self._save_project_settings(project_settings)

    def get_computex_settings(self) -> ComputexSettings:
        """获取完整的 Computex 设置，返回包含 host 和 port 的字典。"""
        return ComputexSettings.from_dict({
            "host": self.get_computex_host(),
            "port": self.get_computex_port()
        })