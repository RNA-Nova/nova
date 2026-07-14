"""SettingsManager 内存工厂测试辅助函数。"""

from typing import Any, Optional

from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.types.config.settings import Settings
from tests._helpers.settings_storage import InMemorySettingsStorage


def settings_manager_in_memory(
    settings: Optional[dict[str, Any]] = None,
    project_trusted: bool = True,
) -> SettingsManager:
    """Create an in-memory SettingsManager (no file I/O)."""
    storage = InMemorySettingsStorage()
    initial = Settings.model_validate(settings) if settings else Settings()
    return SettingsManager(
        storage, initial, Settings(), project_trusted=project_trusted
    )
