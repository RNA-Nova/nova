"""Telemetry helpers for Nova Harness.

Currently only install telemetry is exposed. The actual collection and upload
logic is intentionally kept outside of this module; ``telemetry.py`` only
provides a small predicate to decide whether telemetry is enabled.

Priority:
1. ``NOVA_TELEMETRY`` environment variable if set.
2. ``SettingsManager.get_enable_install_telemetry()`` otherwise.
"""

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _is_truthy_env_flag(value: Optional[str]) -> bool:
    if not value:
        return False
    return value == "1" or value.lower() in ("true", "yes")


def is_install_telemetry_enabled(
    settings_manager,
    telemetry_env: Optional[str] = None,
) -> bool:
    """Return whether install telemetry should be enabled.

    Args:
        settings_manager: A ``SettingsManager``-like object exposing
            ``get_enable_install_telemetry()``.
        telemetry_env: Value of the ``NOVA_TELEMETRY`` environment variable.
            Defaults to ``os.environ.get("NOVA_TELEMETRY")``.

    Returns:
        True when telemetry is enabled.
    """
    if telemetry_env is None:
        telemetry_env = os.environ.get("NOVA_TELEMETRY")
    if telemetry_env is not None:
        return _is_truthy_env_flag(telemetry_env)
    return bool(settings_manager.get_enable_install_telemetry())


def report_install_telemetry(
    settings_manager,
    event: str,
    payload: Optional[Dict[str, Any]] = None,
    telemetry_env: Optional[str] = None,
) -> None:
    """记录一次 install telemetry 事件。

    - 先检查 ``is_install_telemetry_enabled()``，未开启时直接返回。
    - 开启时将事件记录到日志；未来可在此扩展为上报到远程端点。

    Args:
        settings_manager: ``SettingsManager`` 实例。
        event: 事件名，例如 ``"package_install"``、``"cli_first_run"``。
        payload: 可选的附加字段。
        telemetry_env: 可选的环境变量值，用于测试。
    """
    if not is_install_telemetry_enabled(settings_manager, telemetry_env=telemetry_env):
        return

    record = {"event": event}
    if payload:
        record.update(payload)
    logger.info("install telemetry: %s", record)


# ---------------------------------------------------------------------------
# User analytics
# ---------------------------------------------------------------------------


def is_analytics_enabled(
    settings_manager,
    analytics_env: Optional[str] = None,
) -> bool:
    """Return whether user analytics should be enabled.

    Priority:
    1. ``NOVA_ANALYTICS`` environment variable if set.
    2. ``SettingsManager.get_enable_analytics()`` otherwise.

    Args:
        settings_manager: A ``SettingsManager``-like object exposing
            ``get_enable_analytics()``.
        analytics_env: Value of the ``NOVA_ANALYTICS`` environment variable.
            Defaults to ``os.environ.get("NOVA_ANALYTICS")``.

    Returns:
        True when analytics is enabled.
    """
    if analytics_env is None:
        analytics_env = os.environ.get("NOVA_ANALYTICS")
    if analytics_env is not None:
        return _is_truthy_env_flag(analytics_env)
    return bool(settings_manager.get_enable_analytics())


def report_analytics_event(
    settings_manager,
    event: str,
    payload: Optional[Dict[str, Any]] = None,
    analytics_env: Optional[str] = None,
) -> None:
    """记录一次用户 analytics 事件。

    - 先检查 ``is_analytics_enabled()``，未开启时直接返回。
    - 开启时将事件记录到日志；未来可在此扩展为上报到远程端点。

    Args:
        settings_manager: ``SettingsManager`` 实例。
        event: 事件名，例如 ``"session_started"``、``"message_sent"``。
        payload: 可选的附加字段。``tracking_id`` 会自动附加。
        analytics_env: 可选的环境变量值，用于测试。
    """
    if not is_analytics_enabled(settings_manager, analytics_env=analytics_env):
        return

    record: Dict[str, Any] = {"event": event}
    tracking_id = settings_manager.get_tracking_id()
    if tracking_id:
        record["tracking_id"] = tracking_id
    if payload:
        record.update(payload)
    logger.info("analytics: %s", record)


__all__ = [
    "is_install_telemetry_enabled",
    "report_install_telemetry",
    "is_analytics_enabled",
    "report_analytics_event",
]
