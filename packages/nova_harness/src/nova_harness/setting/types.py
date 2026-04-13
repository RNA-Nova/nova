"""
Settings types and data classes.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Union

from mashumaro.mixins.json import DataClassJSONMixin

from nova_ai import Transport,ThinkingLevel


class SettingsScope(str, Enum):
    """Settings scope enumeration."""
    GLOBAL = "global"
    PROJECT = "project"


@dataclass
class CompactionSettings(DataClassJSONMixin):
    """Compaction configuration settings."""
    enabled: Optional[bool] = None
    reserve_tokens: Optional[int] = None
    keep_recent_tokens: Optional[int] = None


@dataclass
class BranchSummarySettings(DataClassJSONMixin):
    """Branch summary configuration settings."""
    reserve_tokens: Optional[int] = None


@dataclass
class RetrySettings(DataClassJSONMixin):
    """Retry configuration settings."""
    enabled: Optional[bool] = None
    max_retries: Optional[int] = None
    base_delay_ms: Optional[int] = None
    max_delay_ms: Optional[int] = None


@dataclass
class TerminalSettings(DataClassJSONMixin):
    """Terminal display settings."""
    show_images: Optional[bool] = None
    clear_on_shrink: Optional[bool] = None


@dataclass
class ImageSettings(DataClassJSONMixin):
    """Image processing settings."""
    auto_resize: Optional[bool] = None
    block_images: Optional[bool] = None


@dataclass
class ThinkingBudgetsSettings(DataClassJSONMixin):
    """Thinking budget configuration."""
    minimal: Optional[int] = None
    low: Optional[int] = None
    medium: Optional[int] = None
    high: Optional[int] = None


@dataclass
class MarkdownSettings(DataClassJSONMixin):
    """Markdown rendering settings."""
    code_block_indent: Optional[str] = None


PackageSource = Union[
    str,
    dict[str, Any]  # With keys: source, extensions, skills, prompts, themes
]

@dataclass
class ComputexSettings(DataClassJSONMixin):
    """Computex server configuration settings."""
    host: Optional[str] = None
    port: Optional[int] = None

@dataclass
class Settings(DataClassJSONMixin):
    """Main settings data class."""
    last_changelog_version: Optional[str] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    default_thinking_level: Optional[ThinkingLevel] = None
    transport: Optional["Transport"] = None  # 字符串注解避免循环导入问题
    steering_mode: Optional[Literal["all", "one-at-a-time"]] = None
    follow_up_mode: Optional[Literal["all", "one-at-a-time"]] = None
    theme: Optional[str] = None
    compaction: Optional[CompactionSettings] = None
    branch_summary: Optional[BranchSummarySettings] = None
    retry: Optional[RetrySettings] = None
    hide_thinking_block: Optional[bool] = None
    shell_path: Optional[str] = None
    quiet_startup: Optional[bool] = None
    shell_command_prefix: Optional[str] = None
    collapse_changelog: Optional[bool] = None
    packages: Optional[list[PackageSource]] = None
    extensions: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    prompts: Optional[list[str]] = None
    themes: Optional[list[str]] = None
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
    computex: Optional[ComputexSettings] = None 


@dataclass
class SettingsError:
    """Settings error container."""
    scope: SettingsScope
    error: Exception