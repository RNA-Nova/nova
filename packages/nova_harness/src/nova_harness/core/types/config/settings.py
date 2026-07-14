"""
设置类型定义。

对应原 `nova_harness.setting.types`。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Optional, Union

from nova_ai import ThinkingLevel, Transport
from nova_ai.types.base_model import NovaBaseModel

from nova_harness.core.types.compaction import CompactionSettings


class SettingsScope(str, Enum):
    """Settings scope enumeration."""

    GLOBAL = "global"
    PROJECT = "project"


class BranchSummarySettings(NovaBaseModel):
    """Branch summary configuration settings."""

    reserve_tokens: Optional[int] = None


DefaultProjectTrust = Literal["ask", "always", "never"]


class ProviderRetrySettings(NovaBaseModel):
    """Provider-level retry configuration.

    控制 SDK/provider 请求的超时、重试次数与最大重试延迟。
    """

    timeout_ms: Optional[int] = None
    max_retries: Optional[int] = None
    max_retry_delay_ms: Optional[int] = None


class RetrySettings(NovaBaseModel):
    """Retry configuration settings."""

    enabled: Optional[bool] = None
    max_retries: Optional[int] = None
    base_delay_ms: Optional[int] = None
    provider: Optional[ProviderRetrySettings] = None


class TerminalSettings(NovaBaseModel):
    """Terminal display settings."""

    show_images: Optional[bool] = None
    clear_on_shrink: Optional[bool] = None


class ImageSettings(NovaBaseModel):
    """Image processing settings."""

    auto_resize: Optional[bool] = None
    block_images: Optional[bool] = None


class ThinkingBudgetsSettings(NovaBaseModel):
    """Thinking budget configuration."""

    minimal: Optional[int] = None
    low: Optional[int] = None
    medium: Optional[int] = None
    high: Optional[int] = None


class MarkdownSettings(NovaBaseModel):
    """Markdown rendering settings."""

    code_block_indent: Optional[str] = None


PackageSourceSpec = Union[
    str,
    dict[
        str, Any
    ],  # With keys: source, and optional resource filters (extensions, skills, prompts, agents, tools)
]


class Settings(NovaBaseModel):
    """Main settings Pydantic model."""

    last_changelog_version: Optional[str] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    default_thinking_level: Optional[ThinkingLevel] = None
    transport: Optional["Transport"] = None  # 字符串注解避免循环导入问题
    steering_mode: Optional[Literal["all", "one-at-a-time"]] = None
    follow_up_mode: Optional[Literal["all", "one-at-a-time"]] = None
    compaction: Optional[CompactionSettings] = None
    branch_summary: Optional[BranchSummarySettings] = None
    retry: Optional[RetrySettings] = None
    hide_thinking_block: Optional[bool] = None
    shell_path: Optional[str] = None
    quiet_startup: Optional[bool] = None
    shell_command_prefix: Optional[str] = None
    collapse_changelog: Optional[bool] = None
    default_project_trust: Optional[DefaultProjectTrust] = None
    packages: Optional[list[PackageSourceSpec]] = None
    extensions: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    prompts: Optional[list[str]] = None
    themes: Optional[list[str]] = None
    agents: Optional[list[str]] = None
    enable_skill_commands: Optional[bool] = None
    terminal: Optional[TerminalSettings] = None
    images: Optional[ImageSettings] = None
    enabled_models: Optional[list[str]] = None
    double_escape_action: Optional[Literal["fork", "tree", "none"]] = None
    thinking_budgets: Optional[ThinkingBudgetsSettings] = None
    editor_padding_x: Optional[int] = None
    autocomplete_max_visible: Optional[int] = None
    show_hardware_cursor: Optional[bool] = None
    markdown: Optional[MarkdownSettings] = None
    enable_install_telemetry: Optional[bool] = None
    enable_analytics: Optional[bool] = None
    tracking_id: Optional[str] = None
    http_idle_timeout_ms: Optional[int] = None
    websocket_connect_timeout_ms: Optional[int] = None


@dataclass
class SettingsError:
    """Settings error container."""

    scope: SettingsScope
    error: Exception


__all__ = [
    "SettingsScope",
    "SettingsError",
    "BranchSummarySettings",
    "ProviderRetrySettings",
    "RetrySettings",
    "TerminalSettings",
    "ImageSettings",
    "ThinkingBudgetsSettings",
    "MarkdownSettings",
    "PackageSourceSpec",
    "DefaultProjectTrust",
    "Settings",
]
