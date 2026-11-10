"""
环境变量工具函数
处理API密钥和认证信息的获取
"""

from typing import Optional

from nova_protocol import ProviderEnv

from .provider_env import get_provider_env_value


def get_env_api_key(
    provider: str, provider_env: Optional[ProviderEnv] = None
) -> Optional[str]:
    """读取 provider 的 API key；provider_env 覆盖进程环境。"""
    if provider == "github-copilot":
        for name in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
            value = get_provider_env_value(name, provider_env)
            if value:
                return value
        return None

    if provider == "anthropic":
        for name in ("ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
            value = get_provider_env_value(name, provider_env)
            if value:
                return value
        return None

    # 标准API密钥映射（与 TS env-api-keys.ts 对齐；即使 Python 暂未内置
    # 某些 provider，自定义 models.json 也可能用到这些 provider id）
    env_map = {
        "ant-ling": "ANT_LING_API_KEY",
        "qwen-token-plan": "QWEN_TOKEN_PLAN_API_KEY",
        "qwen-token-plan-cn": "QWEN_TOKEN_PLAN_CN_API_KEY",
        "qwen-token-plan-individual": "QWEN_TOKEN_PLAN_API_KEY",
        "qwen-token-plan-individual-cn": "QWEN_TOKEN_PLAN_CN_API_KEY",
        "openai": "OPENAI_API_KEY",
        "azure": "AZURE_OPENAI_API_KEY",
        "azure-openai-responses": "AZURE_OPENAI_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "google": "GEMINI_API_KEY",
        "google-vertex": "GOOGLE_CLOUD_API_KEY",
        "groq": "GROQ_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
        "xai": "XAI_API_KEY",
        "typesafe": "TYPESAFE_API_KEY",
        "radius": "RADIUS_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "vercel-ai-gateway": "AI_GATEWAY_API_KEY",
        "zai": "ZAI_API_KEY",
        "zai-coding-cn": "ZAI_CODING_CN_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "minimax-cn": "MINIMAX_CN_API_KEY",
        "moonshotai": "MOONSHOT_API_KEY",
        "moonshotai-cn": "MOONSHOT_API_KEY",
        "huggingface": "HF_TOKEN",
        "fireworks": "FIREWORKS_API_KEY",
        "together": "TOGETHER_API_KEY",
        "baseten": "BASETEN_API_KEY",
        "opencode": "OPENCODE_API_KEY",
        "opencode-go": "OPENCODE_API_KEY",
        "kimi-coding": "KIMI_API_KEY",
        "meta": "META_API_KEY",
        "cloudflare-workers-ai": "CLOUDFLARE_API_KEY",
        "cloudflare-ai-gateway": "CLOUDFLARE_API_KEY",
        "xiaomi": "XIAOMI_API_KEY",
        "xiaomi-token-plan-cn": "XIAOMI_TOKEN_PLAN_CN_API_KEY",
        "xiaomi-token-plan-ams": "XIAOMI_TOKEN_PLAN_AMS_API_KEY",
        "xiaomi-token-plan-sgp": "XIAOMI_TOKEN_PLAN_SGP_API_KEY",
        "volcengine": "VOLCENGINE_API_KEY",
    }

    env_var = env_map.get(provider)
    return get_provider_env_value(env_var, provider_env) if env_var else None
