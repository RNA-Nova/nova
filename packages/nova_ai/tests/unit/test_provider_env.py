"""Provider 级环境变量覆盖测试（对齐 TS provider-env.ts）。"""

import os

from nova_ai.utils.provider_env import get_provider_env_value


def _set_env(key: str, value: str):
    original = os.environ.get(key)
    os.environ[key] = value
    return original


def _restore(key: str, original):
    if original is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = original


class TestGetProviderEnvValue:
    def test_override_wins_over_process_env(self):
        original = _set_env("NOVA_TEST_VAR", "process")
        try:
            assert (
                get_provider_env_value("NOVA_TEST_VAR", {"NOVA_TEST_VAR": "scoped"})
                == "scoped"
            )
        finally:
            _restore("NOVA_TEST_VAR", original)

    def test_falls_back_to_process_env(self):
        original = _set_env("NOVA_TEST_VAR2", "process")
        try:
            assert get_provider_env_value("NOVA_TEST_VAR2") == "process"
        finally:
            _restore("NOVA_TEST_VAR2", original)

    def test_empty_override_falls_back(self):
        original = _set_env("NOVA_TEST_VAR3", "process")
        try:
            assert (
                get_provider_env_value("NOVA_TEST_VAR3", {"NOVA_TEST_VAR3": ""})
                == "process"
            )
        finally:
            _restore("NOVA_TEST_VAR3", original)

    def test_missing_returns_none(self):
        os.environ.pop("NOVA_TEST_MISSING", None)
        assert get_provider_env_value("NOVA_TEST_MISSING") is None
        assert get_provider_env_value("NOVA_TEST_MISSING", {}) is None

    def test_empty_process_env_returns_none(self):
        original = _set_env("NOVA_TEST_EMPTY", "")
        try:
            assert get_provider_env_value("NOVA_TEST_EMPTY") is None
        finally:
            _restore("NOVA_TEST_EMPTY", original)
