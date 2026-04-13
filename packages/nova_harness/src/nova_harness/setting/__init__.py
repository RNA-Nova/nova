"""
Settings management module with file-based and in-memory storage backends.
"""

from .types import (
    CompactionSettings,
    BranchSummarySettings,
    RetrySettings,
    TerminalSettings,
    ImageSettings,
    ThinkingBudgetsSettings,
    MarkdownSettings,
    PackageSource,
    Settings,
    SettingsError,
    SettingsScope,
)
from .storage import SettingsStorage, FileSettingsStorage, InMemorySettingsStorage
from .utils import deep_merge_settings
from .manager import SettingsManager

__all__ = [
    "CompactionSettings",
    "BranchSummarySettings",
    "RetrySettings",
    "TerminalSettings",
    "ImageSettings",
    "ThinkingBudgetsSettings",
    "MarkdownSettings",
    "PackageSource",
    "Settings",
    "SettingsError",
    "SettingsScope",
    "SettingsStorage",
    "FileSettingsStorage",
    "InMemorySettingsStorage",
    "deep_merge_settings",
    "SettingsManager",
]