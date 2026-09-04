"""
设置类型定义。

对应原 `nova_harness.setting.types`。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Optional, Union

from nova_ai import ModelThinkingLevel
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
    """Main settings Pydantic model.

    字段分两类：运行时消费的（模型/重试/压缩/shell/包列表等）与纯前端消费的
    展示偏好（editor/autocomplete/cursor/changelog 等——运行时只负责存储与
    round-trip（getSettings/updateSettings 泛型通道），从不解释其语义。
    """

    last_changelog_version: Optional[str] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    default_thinking_level: Optional[ModelThinkingLevel] = None
    steering_mode: Optional[Literal["all", "one-at-a-time"]] = None
    follow_up_mode: Optional[Literal["all", "one-at-a-time"]] = None
    compaction: Optional[CompactionSettings] = None
    branch_summary: Optional[BranchSummarySettings] = None
    retry: Optional[RetrySettings] = None
    hide_thinking_block: Optional[bool] = None
    # 是否在 transcript 中显示显著的 prompt 缓存 miss 提醒（默认 false）
    show_cache_miss_notices: Optional[bool] = None
    shell_path: Optional[str] = None
    quiet_startup: Optional[bool] = None
    shell_command_prefix: Optional[str] = None
    collapse_changelog: Optional[bool] = None
    default_project_trust: Optional[DefaultProjectTrust] = None
    packages: Optional[list[PackageSourceSpec]] = None
    extensions: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    prompts: Optional[list[str]] = None
    agents: Optional[list[str]] = None
    # tools / user_tools：名字 pattern（plain/!/+/-，四级名单代数——
    # 工具只来自已安装包，无路径可加，故纯名字开关）；
    # personas：路径 + pattern（与 skills 同形态，persona 升格后生效）
    tools: Optional[list[str]] = None
    user_tools: Optional[list[str]] = None
    personas: Optional[list[str]] = None
    # 角色边界：open（默认，yaml 名单 = 初始激活集，面板可见全池）/
    # strict（yaml 名单 = 注册表硬闸门，面板只见角色内）
    role_boundary: Optional[Literal["open", "strict"]] = None
    enable_skill_commands: Optional[bool] = None
    terminal: Optional[TerminalSettings] = None
    images: Optional[ImageSettings] = None
    enabled_models: Optional[list[str]] = None
    double_escape_action: Optional[Literal["fork", "tree", "none"]] = None
    # TUI 主题名（纯前端消费——运行时只 round-trip 存储，从不解释语义）
    theme: Optional[str] = None
    # 外部编辑器命令（ctrl+g；纯前端消费——缺省读 $VISUAL/$EDITOR/vi）
    external_editor: Optional[str] = None
    thinking_budgets: Optional[ThinkingBudgetsSettings] = None
    editor_padding_x: Optional[int] = None
    autocomplete_max_visible: Optional[int] = None
    show_hardware_cursor: Optional[bool] = None
    markdown: Optional[MarkdownSettings] = None
    enable_install_telemetry: Optional[bool] = None
    http_idle_timeout_ms: Optional[int] = None
    # 用户层命令排除集（slash 命令黑名单——与 agent.yaml 的 commands
    # 允许集求交：先过 agent 允许集，再扣本排除集）
    disabled_commands: Optional[list[str]] = None


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
