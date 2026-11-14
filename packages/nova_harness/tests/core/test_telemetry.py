"""Tests for telemetry helpers."""

from unittest.mock import MagicMock

import pytest

from nova_harness.core.utils.telemetry import is_install_telemetry_enabled


@pytest.fixture
def settings():
    return MagicMock(get_enable_install_telemetry=lambda: False)


def test_env_true_enables_telemetry(settings):
    assert is_install_telemetry_enabled(settings, telemetry_env="1") is True
    assert is_install_telemetry_enabled(settings, telemetry_env="true") is True
    assert is_install_telemetry_enabled(settings, telemetry_env="yes") is True
    assert is_install_telemetry_enabled(settings, telemetry_env="True") is True


def test_env_false_disables_telemetry(settings):
    settings.get_enable_install_telemetry = lambda: True
    assert is_install_telemetry_enabled(settings, telemetry_env="0") is False
    assert is_install_telemetry_enabled(settings, telemetry_env="false") is False
    assert is_install_telemetry_enabled(settings, telemetry_env="no") is False


def test_env_none_falls_back_to_settings(settings):
    settings.get_enable_install_telemetry = lambda: True
    assert is_install_telemetry_enabled(settings, telemetry_env=None) is True

    settings.get_enable_install_telemetry = lambda: False
    assert is_install_telemetry_enabled(settings, telemetry_env=None) is False
