"""环境变量工具测试（对齐 TS env-api-keys.ts 映射）。"""

import pytest
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

    @pytest.mark.parametrize(
        ("provider", "variable"),
        [
            ("ant-ling", "ANT_LING_API_KEY"),
            ("qwen-token-plan", "QWEN_TOKEN_PLAN_API_KEY"),
            ("qwen-token-plan-cn", "QWEN_TOKEN_PLAN_CN_API_KEY"),
            ("qwen-token-plan-individual", "QWEN_TOKEN_PLAN_API_KEY"),
            ("qwen-token-plan-individual-cn", "QWEN_TOKEN_PLAN_CN_API_KEY"),
            ("openai", "OPENAI_API_KEY"),
            ("azure", "AZURE_OPENAI_API_KEY"),
            ("azure-openai-responses", "AZURE_OPENAI_API_KEY"),
            ("nvidia", "NVIDIA_API_KEY"),
            ("deepseek", "DEEPSEEK_API_KEY"),
            ("google", "GEMINI_API_KEY"),
            ("google-vertex", "GOOGLE_CLOUD_API_KEY"),
            ("groq", "GROQ_API_KEY"),
            ("cerebras", "CEREBRAS_API_KEY"),
            ("xai", "XAI_API_KEY"),
            ("typesafe", "TYPESAFE_API_KEY"),
            ("radius", "RADIUS_API_KEY"),
            ("openrouter", "OPENROUTER_API_KEY"),
            ("vercel-ai-gateway", "AI_GATEWAY_API_KEY"),
            ("zai", "ZAI_API_KEY"),
            ("zai-coding-cn", "ZAI_CODING_CN_API_KEY"),
            ("mistral", "MISTRAL_API_KEY"),
            ("minimax", "MINIMAX_API_KEY"),
            ("minimax-cn", "MINIMAX_CN_API_KEY"),
            ("moonshotai", "MOONSHOT_API_KEY"),
            ("moonshotai-cn", "MOONSHOT_API_KEY"),
            ("huggingface", "HF_TOKEN"),
            ("fireworks", "FIREWORKS_API_KEY"),
            ("together", "TOGETHER_API_KEY"),
            ("baseten", "BASETEN_API_KEY"),
            ("opencode", "OPENCODE_API_KEY"),
            ("opencode-go", "OPENCODE_API_KEY"),
            ("kimi-coding", "KIMI_API_KEY"),
            ("meta", "META_API_KEY"),
            ("cloudflare-workers-ai", "CLOUDFLARE_API_KEY"),
            ("cloudflare-ai-gateway", "CLOUDFLARE_API_KEY"),
            ("xiaomi", "XIAOMI_API_KEY"),
            ("xiaomi-token-plan-cn", "XIAOMI_TOKEN_PLAN_CN_API_KEY"),
            ("xiaomi-token-plan-ams", "XIAOMI_TOKEN_PLAN_AMS_API_KEY"),
            ("xiaomi-token-plan-sgp", "XIAOMI_TOKEN_PLAN_SGP_API_KEY"),
            ("volcengine", "VOLCENGINE_API_KEY"),
        ],
    )
    def test_every_standard_mapping(self, monkeypatch, provider, variable):
        monkeypatch.setenv(variable, "synthetic-token")
        assert get_env_api_key(provider) == "synthetic-token"
