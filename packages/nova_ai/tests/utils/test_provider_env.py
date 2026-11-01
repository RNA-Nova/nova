"""Provider 级环境变量覆盖测试（对齐 TS provider-env.ts）。"""

from nova_ai.utils.provider_env import get_provider_env_value


class TestGetProviderEnvValue:
    def test_override_wins_over_process_env(self, monkeypatch):
        monkeypatch.setenv("NOVA_TEST_VAR", "process")
        assert (
            get_provider_env_value("NOVA_TEST_VAR", {"NOVA_TEST_VAR": "scoped"})
            == "scoped"
        )

    def test_falls_back_to_process_env(self, monkeypatch):
        monkeypatch.setenv("NOVA_TEST_VAR2", "process")
        assert get_provider_env_value("NOVA_TEST_VAR2") == "process"

    def test_empty_override_falls_back(self, monkeypatch):
        monkeypatch.setenv("NOVA_TEST_VAR3", "process")
        assert (
            get_provider_env_value("NOVA_TEST_VAR3", {"NOVA_TEST_VAR3": ""})
            == "process"
        )

    def test_missing_returns_none(self, monkeypatch):
        monkeypatch.delenv("NOVA_TEST_MISSING", raising=False)
        assert get_provider_env_value("NOVA_TEST_MISSING") is None
        assert get_provider_env_value("NOVA_TEST_MISSING", {}) is None

    def test_empty_process_env_returns_none(self, monkeypatch):
        monkeypatch.setenv("NOVA_TEST_EMPTY", "")
        assert get_provider_env_value("NOVA_TEST_EMPTY") is None
