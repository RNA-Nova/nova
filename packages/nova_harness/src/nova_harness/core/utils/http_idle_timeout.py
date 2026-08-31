"""HTTP idle timeout configuration for Nova Harness.

提供默认超时值、解析/格式化辅助函数，并支持通过 ``NOVA_HTTP_IDLE_TIMEOUT_MS``
环境变量覆盖。超时值由调用方在创建模型 API 请求时使用（例如作为 ``nova_ai``
invoke 选项的 ``timeout``）。
"""

import os
from typing import Optional

DEFAULT_HTTP_IDLE_TIMEOUT_MS = 300_000

HTTP_IDLE_TIMEOUT_CHOICES = [
    {"label": "30 sec", "timeout_ms": 30_000},
    {"label": "1 min", "timeout_ms": 60_000},
    {"label": "2 min", "timeout_ms": 120_000},
    {"label": "5 min", "timeout_ms": 300_000},
    {"label": "disabled", "timeout_ms": 0},
]


def format_http_idle_timeout_ms(timeout_ms: int) -> str:
    """将毫秒超时值格式化为可展示字符串。"""
    if timeout_ms == 0:
        return "disabled"
    for choice in HTTP_IDLE_TIMEOUT_CHOICES:
        if choice["timeout_ms"] == timeout_ms:
            return choice["label"]
    if timeout_ms < 1_000:
        return f"{timeout_ms} ms"
    if timeout_ms < 60_000:
        return f"{timeout_ms // 1_000} sec"
    return f"{timeout_ms // 60_000} min"


def parse_http_idle_timeout_ms(value: object) -> Optional[int]:
    """Parse a timeout value into milliseconds.

    Accepts integers, floats, or the string ``"disabled"``.
    Returns ``None`` for invalid values.
    """
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.lower() == "disabled":
            return 0
        if not trimmed:
            return None
        try:
            return parse_http_idle_timeout_ms(float(trimmed))
        except ValueError:
            return None

    if not isinstance(value, (int, float)):
        return None
    if value < 0 or value != value:  # NaN check
        return None
    return int(value)


def get_http_idle_timeout_ms(settings_manager) -> int:
    """Return the effective HTTP idle timeout in milliseconds.

    Priority:
    1. ``NOVA_HTTP_IDLE_TIMEOUT_MS`` environment variable if set and valid.
    2. ``SettingsManager.get_http_idle_timeout_ms()`` otherwise.
    3. ``DEFAULT_HTTP_IDLE_TIMEOUT_MS`` as fallback.
    """
    env_value = os.environ.get("NOVA_HTTP_IDLE_TIMEOUT_MS")
    if env_value is not None:
        parsed = parse_http_idle_timeout_ms(env_value)
        if parsed is not None:
            return parsed

    try:
        return int(settings_manager.get_http_idle_timeout_ms())
    except Exception:
        return DEFAULT_HTTP_IDLE_TIMEOUT_MS


# 用户禁用空闲超时时传给 HTTP 客户端的最大超时值（约 24.8 天）。
MAX_HTTP_IDLE_TIMEOUT_MS = 2_147_483_647


def get_http_idle_timeout_seconds(settings_manager) -> float:
    """Return the effective HTTP idle timeout in seconds for HTTP clients.

    A setting of ``0`` (disabled) is converted to a very large value so the
    client effectively never times out.
    """
    timeout_ms = get_http_idle_timeout_ms(settings_manager)
    if timeout_ms == 0:
        return MAX_HTTP_IDLE_TIMEOUT_MS / 1000
    return timeout_ms / 1000


__all__ = [
    "DEFAULT_HTTP_IDLE_TIMEOUT_MS",
    "HTTP_IDLE_TIMEOUT_CHOICES",
    "MAX_HTTP_IDLE_TIMEOUT_MS",
    "parse_http_idle_timeout_ms",
    "format_http_idle_timeout_ms",
    "get_http_idle_timeout_ms",
    "get_http_idle_timeout_seconds",
]
