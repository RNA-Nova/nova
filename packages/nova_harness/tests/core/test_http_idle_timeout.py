"""Tests for HTTP idle timeout helpers."""

import os
from unittest.mock import MagicMock, patch

import pytest

from nova_harness.core.config.http_idle_timeout import (
    DEFAULT_HTTP_IDLE_TIMEOUT_MS,
    MAX_HTTP_IDLE_TIMEOUT_MS,
    get_http_idle_timeout_ms,
    get_http_idle_timeout_seconds,
    parse_http_idle_timeout_ms,
)


def test_parse_timeout_ms_accepts_numbers():
    assert parse_http_idle_timeout_ms(30_000) == 30_000
    assert parse_http_idle_timeout_ms("300000") == 300_000
    assert parse_http_idle_timeout_ms("disabled") == 0
    assert parse_http_idle_timeout_ms("DISABLED") == 0


def test_parse_timeout_ms_rejects_invalid():
    assert parse_http_idle_timeout_ms("not-a-number") is None
    assert parse_http_idle_timeout_ms(-1) is None
    assert parse_http_idle_timeout_ms(None) is None
    assert parse_http_idle_timeout_ms("") is None


def test_get_timeout_ms_from_env():
    settings = MagicMock(get_http_idle_timeout_ms=lambda: 60_000)
    with patch.dict(os.environ, {"NOVA_HTTP_IDLE_TIMEOUT_MS": "120000"}):
        assert get_http_idle_timeout_ms(settings) == 120_000


def test_get_timeout_ms_from_settings():
    settings = MagicMock(get_http_idle_timeout_ms=lambda: 60_000)
    with patch.dict(os.environ, {}, clear=True):
        assert get_http_idle_timeout_ms(settings) == 60_000


def test_get_timeout_ms_invalid_env_falls_back():
    settings = MagicMock(get_http_idle_timeout_ms=lambda: 90_000)
    with patch.dict(os.environ, {"NOVA_HTTP_IDLE_TIMEOUT_MS": "bad"}):
        assert get_http_idle_timeout_ms(settings) == 90_000


def test_get_timeout_ms_default_when_settings_fail():
    settings = MagicMock(get_http_idle_timeout_ms=lambda: int("bad"))
    with patch.dict(os.environ, {}, clear=True):
        assert get_http_idle_timeout_ms(settings) == DEFAULT_HTTP_IDLE_TIMEOUT_MS


def test_get_timeout_seconds_converts_ms():
    settings = MagicMock(get_http_idle_timeout_ms=lambda: 60_000)
    with patch.dict(os.environ, {}, clear=True):
        assert get_http_idle_timeout_seconds(settings) == 60.0


def test_get_timeout_seconds_disabled_uses_max():
    settings = MagicMock(get_http_idle_timeout_ms=lambda: 0)
    with patch.dict(os.environ, {}, clear=True):
        assert (
            get_http_idle_timeout_seconds(settings) == MAX_HTTP_IDLE_TIMEOUT_MS / 1000
        )
