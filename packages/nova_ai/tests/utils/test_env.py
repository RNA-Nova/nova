"""环境变量工具测试（对齐 TS env-api-keys.ts 映射）。"""

from nova_ai.utils.env import get_env_api_key


class TestGetEnvApiKey:
    """API 密钥环境变量获取测试"""

    def test_openai_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        assert get_env_api_key("openai") == "sk-openai-test"

    def test_anthropic_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-test")
        assert get_env_api_key("anthropic") == "sk-anthropic-test"

    def test_volcengine_key(self, monkeypatch):
        monkeypatch.setenv("VOLCENGINE_API_KEY", "sk-volc-test")
        assert get_env_api_key("volcengine") == "sk-volc-test"

    def test_copilot_key(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp-test")
        assert get_env_api_key("github-copilot") == "ghp-test"

    def test_unknown_provider(self):
        assert get_env_api_key("unknown-provider") is None

    def test_not_set(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert get_env_api_key("openai") is None

    def test_provider_scoped_env_wins(self, monkeypatch):
        monkeypatch.setenv("BASETEN_API_KEY", "process-key")
        assert (
            get_env_api_key("baseten", {"BASETEN_API_KEY": "scoped-key"})
            == "scoped-key"
        )
