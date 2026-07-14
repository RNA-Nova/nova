"""
Settings management module with file-based and in-memory storage backends.
"""

import asyncio
import json
import logging
import os
import threading
import uuid
from copy import deepcopy
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

from nova_ai import ThinkingLevel, Transport

from nova_harness.core.config.defaults import get_agent_dir, get_project_base_dir
from nova_harness.core.config.settings.storage import (
    FileSettingsStorage,
    SettingsStorage,
)
from nova_harness.core.config.settings.utils import deep_merge_settings
from nova_harness.core.types.compaction import CompactionSettings
from nova_harness.core.types.config.settings import (
    BranchSummarySettings,
    DefaultProjectTrust,
    ImageSettings,
    PackageSourceSpec,
    RetrySettings,
    Settings,
    SettingsError,
    SettingsScope,
    TerminalSettings,
    ThinkingBudgetsSettings,
)
from nova_harness.core.types.project_trust import ProjectNotTrustedError


class SettingsManager:
    """Manages application settings with file-based and in-memory storage backends."""

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

    def __init__(
        self,
        storage: SettingsStorage,
        initial_global: Settings,
        initial_project: Settings,
        global_load_error: Optional[Exception] = None,
        project_load_error: Optional[Exception] = None,
        initial_errors: Optional[list[SettingsError]] = None,
        project_trusted: bool = True,
    ) -> None:
        self._storage = storage
        self._global_settings = initial_global
        self._project_settings = initial_project
        self._global_settings_load_error = global_load_error
        self._project_settings_load_error = project_load_error
        self._errors = initial_errors or []
        self._project_trusted = project_trusted

        self._settings = deep_merge_settings(
            self._global_settings, self._project_settings
        )
        self._modified_fields: set[str] = set()
        self._modified_nested_fields: dict[str, set[str]] = {}
        self._modified_project_fields: set[str] = set()
        self._modified_project_nested_fields: dict[str, set[str]] = {}
        self._write_queue: list[Callable[[], None]] = []
        self._write_lock = threading.Lock()
        self._settings_lock = threading.RLock()

    @classmethod
    def create(
        cls,
        cwd: str = os.getcwd(),
        agent_dir: str = str(get_agent_dir()),
        project_trusted: bool = True,
    ) -> "SettingsManager":
        """Create a SettingsManager that loads from files."""
        storage = FileSettingsStorage(cwd, agent_dir)
        return cls.from_storage(storage, project_trusted=project_trusted)

    @classmethod
    def from_storage(
        cls, storage: SettingsStorage, project_trusted: bool = True
    ) -> "SettingsManager":
        """Create a SettingsManager from an arbitrary storage backend."""
        global_load = cls._try_load_from_storage(
            storage, SettingsScope.GLOBAL, project_trusted=True
        )
        project_load = cls._try_load_from_storage(
            storage, SettingsScope.PROJECT, project_trusted=project_trusted
        )

        initial_errors: list[SettingsError] = []
        if global_load["error"]:
            initial_errors.append(
                SettingsError(SettingsScope.GLOBAL, global_load["error"])
            )
        if project_load["error"]:
            initial_errors.append(
                SettingsError(SettingsScope.PROJECT, project_load["error"])
            )

        return cls(
            storage,
            global_load["settings"],
            project_load["settings"],
            global_load["error"],
            project_load["error"],
            initial_errors,
            project_trusted=project_trusted,
        )

    @staticmethod
    def migrate_settings(data: dict) -> dict:
        """Migrate old settings format to the current schema.

        处理已知历史字段重命名与格式变更。Python Settings 使用 snake_case，
        因此迁移目标字段也使用 snake_case。
        """
        if not isinstance(data, dict):
            return data

        # Migrate queueMode -> steering_mode
        if "queueMode" in data and "steering_mode" not in data:
            data["steering_mode"] = data.pop("queueMode")

        # Migrate legacy websockets boolean -> transport enum.
        # transport 已固定为 sse，因此无论原值如何都归一化为 sse。
        if "transport" not in data and isinstance(data.get("websockets"), bool):
            data.pop("websockets")
            data["transport"] = "sse"

        # Migrate old skills object format to new array format
        skills = data.get("skills")
        if isinstance(skills, dict) and skills is not None:
            skills_settings = skills
            if (
                "enableSkillCommands" in skills_settings
                and "enable_skill_commands" not in data
            ):
                data["enable_skill_commands"] = skills_settings["enableSkillCommands"]
            if isinstance(skills_settings.get("customDirectories"), list):
                data["skills"] = skills_settings["customDirectories"]
            else:
                data.pop("skills", None)

        # Migrate retry.maxDelayMs -> retry.provider.max_retry_delay_ms
        retry = data.get("retry")
        if isinstance(retry, dict) and retry is not None:
            provider = retry.get("provider")
            if not isinstance(provider, dict) or provider is None:
                provider = {}
                retry["provider"] = provider
            if "maxDelayMs" in retry and provider.get("max_retry_delay_ms") is None:
                provider["max_retry_delay_ms"] = retry.pop("maxDelayMs")
            # Also normalize legacy flat max_delay_ms if present.
            if "max_delay_ms" in retry and provider.get("max_retry_delay_ms") is None:
                provider["max_retry_delay_ms"] = retry.pop("max_delay_ms")

        return data

    @staticmethod
    def _load_from_storage(
        storage: SettingsStorage,
        scope: SettingsScope,
        project_trusted: bool = True,
    ) -> Settings:
        """Load settings from storage."""
        if scope == SettingsScope.PROJECT and not project_trusted:
            return Settings()

        content: Optional[str] = None

        def getter(current: Optional[str]) -> Optional[str]:
            nonlocal content
            content = current
            return None

        storage.with_lock(scope, getter)

        if not content:
            return Settings()

        data = json.loads(content)
        data = SettingsManager.migrate_settings(data)
        return Settings.model_validate(data)

    @classmethod
    def _try_load_from_storage(
        cls,
        storage: SettingsStorage,
        scope: SettingsScope,
        project_trusted: bool = True,
    ) -> dict[str, Any]:
        """Try to load settings, catching errors."""
        try:
            return {
                "settings": cls._load_from_storage(
                    storage, scope, project_trusted=project_trusted
                ),
                "error": None,
            }
        except Exception as e:
            return {"settings": Settings(), "error": e}

    def get_global_settings(self) -> Settings:
        """Return a copy of global settings."""
        return deepcopy(self._global_settings)

    def get_project_settings(self) -> Settings:
        """Return a copy of project settings."""
        return deepcopy(self._project_settings)

    async def reload(self) -> None:
        """Reload settings from storage.

        先 flush 内存中尚未写盘的修改，避免 reload 覆盖丢失。
        """
        await self.flush()

        global_load = self._try_load_from_storage(
            self._storage, SettingsScope.GLOBAL, project_trusted=True
        )
        if not global_load["error"]:
            self._global_settings = global_load["settings"]
            self._global_settings_load_error = None
        else:
            self._global_settings_load_error = global_load["error"]
            self._record_error(SettingsScope.GLOBAL, global_load["error"])

        project_load = self._try_load_from_storage(
            self._storage, SettingsScope.PROJECT, project_trusted=self._project_trusted
        )
        if not project_load["error"]:
            self._project_settings = project_load["settings"]
            self._project_settings_load_error = None
        else:
            self._project_settings_load_error = project_load["error"]
            self._record_error(SettingsScope.PROJECT, project_load["error"])

        self._settings = deep_merge_settings(
            self._global_settings, self._project_settings
        )

        # 已从磁盘重新加载，清空所有修改标记。
        self._modified_fields.clear()
        self._modified_nested_fields.clear()
        self._modified_project_fields.clear()
        self._modified_project_nested_fields.clear()

    def is_project_trusted(self) -> bool:
        """返回当前项目是否被信任。"""
        return self._project_trusted

    def set_project_trusted(self, trusted: bool) -> None:
        """设置项目信任状态。

        设置为不信任时会清空已加载的项目级设置与修改标记，避免未信任项目
        的设置被后续流程使用。设置为信任时则从存储重新加载项目设置，使当前
        进程立即生效。

        直接同步加载 project settings，避免在同步上下文中产生 async 调用。
        """
        if self._project_trusted == trusted:
            return

        self._project_trusted = trusted
        self._modified_project_fields.clear()
        self._modified_project_nested_fields.clear()

        if not trusted:
            self._project_settings = Settings()
            self._project_settings_load_error = None
            self._settings = deep_merge_settings(
                self._global_settings, self._project_settings
            )
            return

        project_load = self._try_load_from_storage(
            self._storage, SettingsScope.PROJECT, project_trusted=trusted
        )
        self._project_settings = project_load["settings"]
        self._project_settings_load_error = project_load["error"]
        if project_load["error"]:
            self._record_error(SettingsScope.PROJECT, project_load["error"])
        self._settings = deep_merge_settings(
            self._global_settings, self._project_settings
        )

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

    def _mark_project_modified(
        self, field: str, nested_key: Optional[str] = None
    ) -> None:
        """Mark a project field as modified during this session."""
        self._modified_project_fields.add(field)
        if nested_key:
            if field not in self._modified_project_nested_fields:
                self._modified_project_nested_fields[field] = set()
            self._modified_project_nested_fields[field].add(nested_key)

    def _record_error(self, scope: SettingsScope, error: Exception) -> None:
        """Record an error."""
        self._errors.append(SettingsError(scope=scope, error=error))

    def _clear_modified_scope(self, scope: SettingsScope) -> None:
        """Clear modified tracking for a scope."""
        if scope == SettingsScope.GLOBAL:
            self._modified_fields.clear()
            self._modified_nested_fields.clear()
        else:
            self._modified_project_fields.clear()
            self._modified_project_nested_fields.clear()

    def _enqueue_write(self, task: Callable[[], None]) -> None:
        """Enqueue a write task."""
        with self._write_lock:
            self._write_queue.append(task)

    def _clone_modified_nested(
        self, source: dict[str, set[str]]
    ) -> dict[str, set[str]]:
        """Clone the modified nested fields tracking."""
        return {k: set(v) for k, v in source.items()}

    def _persist_scoped_settings(
        self,
        scope: SettingsScope,
        snapshot_settings: Settings,
        modified_fields: set[str],
        modified_nested: dict[str, set[str]],
    ) -> None:
        """Persist settings for a specific scope."""

        def writer(current: Optional[str]) -> Optional[str]:
            current_file_settings = {}
            if current:
                current_file_settings = json.loads(current)
                current_file_settings = SettingsManager.migrate_settings(
                    current_file_settings
                )

            merged_settings = dict(current_file_settings)

            for field in modified_fields:
                value = getattr(snapshot_settings, field)
                if (
                    field in modified_nested
                    and value is not None
                    and isinstance(value, BaseModel)
                ):
                    nested_modified = modified_nested[field]
                    base_nested = current_file_settings.get(field, {})
                    if isinstance(base_nested, dict):
                        merged_nested = dict(base_nested)
                        for nested_key in nested_modified:
                            if hasattr(value, nested_key):
                                # 将嵌套 Pydantic 模型转为 dict
                                nested_val = getattr(value, nested_key)
                                if nested_val is not None and isinstance(
                                    nested_val, BaseModel
                                ):
                                    merged_nested[nested_key] = nested_val.model_dump()
                                else:
                                    merged_nested[nested_key] = nested_val
                        merged_settings[field] = merged_nested
                    else:
                        merged_settings[field] = (
                            value.model_dump()
                            if isinstance(value, BaseModel)
                            else value
                        )
                else:
                    # 使用 Pydantic v2 的 model_dump() 方法
                    if value is not None and isinstance(value, BaseModel):
                        merged_settings[field] = value.model_dump()
                    else:
                        merged_settings[field] = value

            return json.dumps(merged_settings, indent=2, ensure_ascii=False)

        self._storage.with_lock(scope, writer)

    def _save(self) -> None:
        """Save global settings."""
        with self._settings_lock:
            self._settings = deep_merge_settings(
                self._global_settings, self._project_settings
            )

            if self._global_settings_load_error:
                return

            snapshot_global = deepcopy(self._global_settings)
            modified_fields = set(self._modified_fields)
            modified_nested = self._clone_modified_nested(self._modified_nested_fields)

        def task() -> None:
            self._persist_scoped_settings(
                SettingsScope.GLOBAL, snapshot_global, modified_fields, modified_nested
            )
            self._clear_modified_scope(SettingsScope.GLOBAL)

        self._enqueue_write(task)

    def _save_project_settings(self, settings: Settings) -> None:
        """Save project settings."""
        with self._settings_lock:
            self._project_settings = deepcopy(settings)
            self._settings = deep_merge_settings(
                self._global_settings, self._project_settings
            )

            if self._project_settings_load_error:
                return

            snapshot_project = deepcopy(self._project_settings)
            modified_fields = set(self._modified_project_fields)
            modified_nested = self._clone_modified_nested(
                self._modified_project_nested_fields
            )

        def task() -> None:
            self._persist_scoped_settings(
                SettingsScope.PROJECT,
                snapshot_project,
                modified_fields,
                modified_nested,
            )
            self._clear_modified_scope(SettingsScope.PROJECT)

        self._enqueue_write(task)

    async def flush(self) -> None:
        """异步等待所有待写入操作完成。

        返回一个可被 ``await`` 的 coroutine，实际写盘在独立线程中执行，
        避免阻塞事件循环。
        """
        await asyncio.to_thread(self.flush_sync)

    def flush_sync(self) -> None:
        """同步执行所有待写入操作。"""
        with self._write_lock:
            for task in self._write_queue:
                try:
                    task()
                except Exception as e:
                    # Log error but continue with other tasks
                    logger.error("Error flushing settings: %s", e)
            self._write_queue.clear()

    def drain_errors(self) -> list[SettingsError]:
        """Drain and return all recorded errors."""
        drained = self._errors.copy()
        self._errors.clear()
        return drained

    # Getter and setter methods（使用 Pydantic v2 序列化）

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

    def get_default_thinking_level(self) -> Optional[ThinkingLevel]:
        return self._settings.default_thinking_level

    def set_default_thinking_level(self, level: Optional[ThinkingLevel] = None) -> None:
        self._global_settings.default_thinking_level = level
        self._mark_modified("default_thinking_level")
        self._save()

    def get_transport(self) -> "Transport":
        # nova_ai 当前仅实现 SSE，WebSocket 传输未实际支持。
        # 固定返回 "sse"，避免用户设置失效或产生误解。
        return "sse"

    def set_transport(self, transport: "Transport") -> None:
        # transport 已固定为 sse；保留本方法以免外部调用方报错，但写入被忽略。
        # 如需恢复可配置性，需先在 nova_ai 实现 WebSocket 传输。
        return

    def get_compaction_enabled(self) -> bool:
        if (
            self._settings.compaction is not None
            and self._settings.compaction.enabled is not None
        ):
            return self._settings.compaction.enabled
        return self.DEFAULT_COMPACTION_ENABLED

    def set_compaction_enabled(self, enabled: bool) -> None:
        if self._global_settings.compaction is None:
            self._global_settings.compaction = CompactionSettings()
        self._global_settings.compaction.enabled = enabled
        self._mark_modified("compaction", "enabled")
        self._save()

    def get_compaction_reserve_tokens(self) -> int:
        if (
            self._settings.compaction is not None
            and self._settings.compaction.reserve_tokens is not None
        ):
            return self._settings.compaction.reserve_tokens
        return self.DEFAULT_COMPACTION_RESERVE_TOKENS

    def get_compaction_keep_recent_tokens(self) -> int:
        if (
            self._settings.compaction is not None
            and self._settings.compaction.keep_recent_tokens is not None
        ):
            return self._settings.compaction.keep_recent_tokens
        return self.DEFAULT_COMPACTION_KEEP_RECENT_TOKENS

    def get_compaction_settings(self) -> CompactionSettings:
        """获取 compaction 设置，None 值会被替换为默认值。"""
        return CompactionSettings.model_validate(
            {
                "enabled": self.get_compaction_enabled(),
                "reserve_tokens": self.get_compaction_reserve_tokens(),
                "keep_recent_tokens": self.get_compaction_keep_recent_tokens(),
            }
        )

    def get_branch_summary_settings(self) -> BranchSummarySettings:
        """获取 branch summary 设置，保留 None 值。"""
        return BranchSummarySettings(
            reserve_tokens=self.get_branch_summary_reserve_tokens()
        )

    def get_branch_summary_reserve_tokens(self) -> int:
        """获取 branch summary 的 reserve_tokens，使用默认值。"""
        if (
            self._settings.branch_summary is not None
            and self._settings.branch_summary.reserve_tokens is not None
        ):
            return self._settings.branch_summary.reserve_tokens
        return self.DEFAULT_BRANCH_SUMMARY_RESERVE_TOKENS

    def get_retry_enabled(self) -> bool:
        if (
            self._settings.retry is not None
            and self._settings.retry.enabled is not None
        ):
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
        provider = retry.provider if retry is not None else None
        return RetrySettings.model_validate(
            {
                "enabled": self.get_retry_enabled(),
                "max_retries": (
                    retry.max_retries
                    if retry is not None and retry.max_retries is not None
                    else self.DEFAULT_RETRY_MAX_RETRIES
                ),
                "base_delay_ms": (
                    retry.base_delay_ms
                    if retry is not None and retry.base_delay_ms is not None
                    else self.DEFAULT_RETRY_BASE_DELAY_MS
                ),
                "provider": {
                    "timeout_ms": (
                        provider.timeout_ms
                        if provider is not None and provider.timeout_ms is not None
                        else None
                    ),
                    "max_retries": (
                        provider.max_retries
                        if provider is not None and provider.max_retries is not None
                        else None
                    ),
                    "max_retry_delay_ms": (
                        provider.max_retry_delay_ms
                        if provider is not None
                        and provider.max_retry_delay_ms is not None
                        else self.DEFAULT_RETRY_MAX_DELAY_MS
                    ),
                },
            }
        )

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

    def get_default_project_trust(self) -> DefaultProjectTrust:
        """获取全局默认项目信任策略。

        支持 ``ask`` / ``always`` / ``never``。仅全局生效；project scope 不保存该字段。
        """
        return self._global_settings.default_project_trust or "ask"

    def set_default_project_trust(self, trust: DefaultProjectTrust) -> None:
        self._global_settings.default_project_trust = trust
        self._mark_modified("default_project_trust")
        self._save()

    def get_packages(self) -> list[PackageSourceSpec]:
        return (
            list(self._settings.packages) if self._settings.packages is not None else []
        )

    def set_packages(self, packages: list[PackageSourceSpec]) -> None:
        with self._settings_lock:
            self._global_settings.packages = packages
            self._mark_modified("packages")
            self._save()

    def set_project_packages(self, packages: list[PackageSourceSpec]) -> None:
        if not self._project_trusted:
            raise ProjectNotTrustedError("project")
        with self._settings_lock:
            project_settings = deepcopy(self._project_settings)
            project_settings.packages = packages
            self._mark_project_modified("packages")
            self._save_project_settings(project_settings)

    # ------------------------------------------------------------------
    # Package source management (merged from PackageSettingsStore)
    # ------------------------------------------------------------------
    def _package_base_dir(self, local: bool, base_dir: Optional[str] = None) -> str:
        """Return the base directory used for package source normalization."""
        if base_dir is not None:
            return os.path.abspath(os.path.expanduser(base_dir))
        if local:
            return str(get_project_base_dir())
        return str(get_agent_dir())

    def get_package_sources(
        self,
        local: bool = False,
        base_dir: Optional[str] = None,
    ) -> list[PackageSourceSpec]:
        """Return resolved package source specs for the requested scope."""
        from nova_harness.core.package.source import (
            resolve_package_source_from_settings,
        )

        base = self._package_base_dir(local, base_dir)
        if local:
            raw = list(self._project_settings.packages or [])
        else:
            raw = list(self._global_settings.packages or [])
        return [resolve_package_source_from_settings(s, base) for s in raw]

    def get_project_package_sources(
        self, base_dir: Optional[str] = None
    ) -> list[PackageSourceSpec]:
        """Convenience wrapper for project-scope resolved package sources."""
        return self.get_package_sources(local=True, base_dir=base_dir)

    def set_package_sources(
        self,
        sources: list[PackageSourceSpec],
        local: bool = False,
        base_dir: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> None:
        """Persist normalized package source specs for the requested scope."""
        from nova_harness.core.package.source import (
            normalize_package_source_for_settings,
        )

        base = self._package_base_dir(local, base_dir)
        resolve_cwd = cwd if cwd is not None else os.getcwd()
        normalized = [
            normalize_package_source_for_settings(s, base, cwd=resolve_cwd)
            for s in sources
        ]
        if local:
            self.set_project_packages(normalized)
        else:
            self.set_packages(normalized)
        self.flush_sync()

    def set_project_package_sources(
        self,
        sources: list[PackageSourceSpec],
        base_dir: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> None:
        """Convenience wrapper for project-scope package source persistence."""
        self.set_package_sources(sources, local=True, base_dir=base_dir, cwd=cwd)

    def add_package_source(
        self,
        source: PackageSourceSpec,
        local: bool = False,
        base_dir: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> None:
        """Add a source spec, replacing any existing entry with the same identity.

        先对输入 source 做 normalize 再计算 identity，与 ``get_package_sources()`` 返回的
        已解析 spec 比较，避免 ``./foo``、``path:./foo``、绝对路径等不同写法产生重复
        条目。合并与写入仍使用原始 source，由 ``set_package_sources()`` 统一做一次相对化，
        避免二次 normalize 把已相对化的路径按 cwd 再次解析。
        """
        from nova_harness.core.package.source import (
            get_package_identity,
            merge_package_source_specs,
            normalize_package_source_for_settings,
        )

        base = self._package_base_dir(local, base_dir)
        resolve_cwd = cwd if cwd is not None else os.getcwd()
        normalized_source = normalize_package_source_for_settings(
            source, base, cwd=resolve_cwd
        )
        new_identity = get_package_identity(normalized_source, base)
        sources = self.get_package_sources(local=local, base_dir=base)
        new_sources: list[PackageSourceSpec] = []
        replaced = False
        for existing in sources:
            if get_package_identity(existing, base) == new_identity:
                # 保留旧 spec 中的 filters 与 editable，避免重复安装时丢失配置。
                merged = merge_package_source_specs(existing, source)
                new_sources.append(merged)
                replaced = True
            else:
                new_sources.append(existing)
        if not replaced:
            new_sources.append(source)
        self.set_package_sources(
            new_sources, local=local, base_dir=base, cwd=resolve_cwd
        )

    def add_project_package_source(
        self,
        source: PackageSourceSpec,
        base_dir: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> None:
        """Convenience wrapper for adding a project-scope package source."""
        self.add_package_source(source, local=True, base_dir=base_dir, cwd=cwd)

    def remove_package_source(
        self,
        source: PackageSourceSpec,
        local: bool = False,
        base_dir: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> bool:
        """Remove all source specs matching the package identity of *source*."""
        from nova_harness.core.package.source import (
            get_package_identity,
            normalize_package_source_for_settings,
        )

        base = self._package_base_dir(local, base_dir)
        resolve_cwd = cwd if cwd is not None else os.getcwd()
        normalized_source = normalize_package_source_for_settings(
            source, base, cwd=resolve_cwd
        )
        target_identity = get_package_identity(normalized_source, base)
        sources = self.get_package_sources(local=local, base_dir=base)
        new_sources = [
            s for s in sources if get_package_identity(s, base) != target_identity
        ]
        if len(new_sources) == len(sources):
            return False
        self.set_package_sources(
            new_sources, local=local, base_dir=base, cwd=resolve_cwd
        )
        return True

    def remove_project_package_source(
        self,
        source: PackageSourceSpec,
        base_dir: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> bool:
        """Convenience wrapper for removing a project-scope package source."""
        return self.remove_package_source(
            source, local=True, base_dir=base_dir, cwd=cwd
        )

    def get_extension_paths(self) -> list[str]:
        return (
            list(self._settings.extensions)
            if self._settings.extensions is not None
            else []
        )

    def set_extension_paths(self, paths: list[str]) -> None:
        self._global_settings.extensions = paths
        self._mark_modified("extensions")
        self._save()

    def set_project_extension_paths(self, paths: list[str]) -> None:
        if not self._project_trusted:
            raise ProjectNotTrustedError("project")
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
        if not self._project_trusted:
            raise ProjectNotTrustedError("project")
        project_settings = deepcopy(self._project_settings)
        project_settings.skills = paths
        self._mark_project_modified("skills")
        self._save_project_settings(project_settings)

    def get_prompt_template_paths(self) -> list[str]:
        return (
            list(self._settings.prompts) if self._settings.prompts is not None else []
        )

    def set_prompt_template_paths(self, paths: list[str]) -> None:
        self._global_settings.prompts = paths
        self._mark_modified("prompts")
        self._save()

    def set_project_prompt_template_paths(self, paths: list[str]) -> None:
        if not self._project_trusted:
            raise ProjectNotTrustedError("project")
        project_settings = deepcopy(self._project_settings)
        project_settings.prompts = paths
        self._mark_project_modified("prompts")
        self._save_project_settings(project_settings)

    def get_enable_skill_commands(self) -> bool:
        if self._settings.enable_skill_commands is not None:
            return self._settings.enable_skill_commands
        return True

    def set_enable_skill_commands(self, enabled: bool) -> None:
        self._global_settings.enable_skill_commands = enabled
        self._mark_modified("enable_skill_commands")
        self._save()

    DEFAULT_ENABLE_INSTALL_TELEMETRY = True

    def get_enable_install_telemetry(self) -> bool:
        if self._settings.enable_install_telemetry is None:
            return self.DEFAULT_ENABLE_INSTALL_TELEMETRY
        return self._settings.enable_install_telemetry

    DEFAULT_HTTP_IDLE_TIMEOUT_MS = 300_000

    def get_http_idle_timeout_ms(self) -> int:
        if self._settings.http_idle_timeout_ms is None:
            return self.DEFAULT_HTTP_IDLE_TIMEOUT_MS
        return self._settings.http_idle_timeout_ms

    def set_http_idle_timeout_ms(self, timeout_ms: int) -> None:
        self._global_settings.http_idle_timeout_ms = timeout_ms
        self._mark_modified("http_idle_timeout_ms")
        self._save()

    DEFAULT_WEBSOCKET_CONNECT_TIMEOUT_MS = 30_000

    def get_websocket_connect_timeout_ms(self) -> int:
        if self._settings.websocket_connect_timeout_ms is None:
            return self.DEFAULT_WEBSOCKET_CONNECT_TIMEOUT_MS
        return self._settings.websocket_connect_timeout_ms

    def set_websocket_connect_timeout_ms(self, timeout_ms: int) -> None:
        self._global_settings.websocket_connect_timeout_ms = timeout_ms
        self._mark_modified("websocket_connect_timeout_ms")
        self._save()

    def get_enable_analytics(self) -> bool:
        return self._settings.enable_analytics or False

    def get_tracking_id(self) -> Optional[str]:
        return self._settings.tracking_id

    def set_enable_analytics(self, enabled: bool) -> None:
        self._global_settings.enable_analytics = enabled
        self._mark_modified("enable_analytics")
        if enabled and not self._global_settings.tracking_id:
            self._global_settings.tracking_id = str(uuid.uuid4())
            self._mark_modified("tracking_id")
        self._save()

    def set_enable_install_telemetry(self, enabled: bool) -> None:
        self._global_settings.enable_install_telemetry = enabled
        self._mark_modified("enable_install_telemetry")
        self._save()

    def get_thinking_budgets(self) -> Optional[ThinkingBudgetsSettings]:
        return self._settings.thinking_budgets

    def get_show_images(self) -> bool:
        if (
            self._settings.terminal is not None
            and self._settings.terminal.show_images is not None
        ):
            return self._settings.terminal.show_images
        return self.DEFAULT_TERMINAL_SHOW_IMAGES

    def set_show_images(self, show: bool) -> None:
        if self._global_settings.terminal is None:
            self._global_settings.terminal = TerminalSettings()
        self._global_settings.terminal.show_images = show
        self._mark_modified("terminal", "show_images")
        self._save()

    def get_clear_on_shrink(self) -> bool:
        if (
            self._settings.terminal is not None
            and self._settings.terminal.clear_on_shrink is not None
        ):
            return self._settings.terminal.clear_on_shrink
        return os.environ.get("NOVA_CLEAR_ON_SHRINK") == "1"

    def set_clear_on_shrink(self, enabled: bool) -> None:
        if self._global_settings.terminal is None:
            self._global_settings.terminal = TerminalSettings()
        self._global_settings.terminal.clear_on_shrink = enabled
        self._mark_modified("terminal", "clear_on_shrink")
        self._save()

    def get_image_auto_resize(self) -> bool:
        if (
            self._settings.images is not None
            and self._settings.images.auto_resize is not None
        ):
            return self._settings.images.auto_resize
        return self.DEFAULT_IMAGE_AUTO_RESIZE

    def set_image_auto_resize(self, enabled: bool) -> None:
        if self._global_settings.images is None:
            self._global_settings.images = ImageSettings()
        self._global_settings.images.auto_resize = enabled
        self._mark_modified("images", "auto_resize")
        self._save()

    def get_block_images(self) -> bool:
        if (
            self._settings.images is not None
            and self._settings.images.block_images is not None
        ):
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
        return os.environ.get("NOVA_HARDWARE_CURSOR") == "1"

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
        self._global_settings.autocomplete_max_visible = max(
            3, min(20, int(max_visible))
        )
        self._mark_modified("autocomplete_max_visible")
        self._save()

    def get_code_block_indent(self) -> str:
        if (
            self._settings.markdown is not None
            and self._settings.markdown.code_block_indent is not None
        ):
            return self._settings.markdown.code_block_indent
        return self.DEFAULT_CODE_BLOCK_INDENT
