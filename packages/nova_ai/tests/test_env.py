"""
环境变量工具测试
"""

import os
from nova_ai.utils.env import (
    get_env_api_key, get_env_api_key_typed, get_all_env_api_keys,
)
from nova_ai.types import KnownProvider


def _set_env(key: str, value: str):
    """设置环境变量，返回原始值（用于恢复）"""
    original = os.environ.get(key)
    os.environ[key] = value
    return original


def _restore_env(key: str, original):
    """恢复环境变量到原始值"""
    if original is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = original


class TestGetEnvApiKey:
    """API 密钥环境变量获取测试"""

    def test_openai_key(self):
        original = _set_env("OPENAI_API_KEY", "sk-openai-test")
        try:
            assert get_env_api_key("openai") == "sk-openai-test"
        finally:
            _restore_env("OPENAI_API_KEY", original)

    def test_anthropic_key(self):
        original = _set_env("ANTHROPIC_API_KEY", "sk-anthropic-test")
        try:
            assert get_env_api_key("anthropic") == "sk-anthropic-test"
        finally:
            _restore_env("ANTHROPIC_API_KEY", original)

    def test_volcengine_key(self):
        original = _set_env("VOLCENGINE_API_KEY", "sk-volc-test")
        try:
            assert get_env_api_key("volcengine") == "sk-volc-test"
        finally:
            _restore_env("VOLCENGINE_API_KEY", original)

    def test_copilot_key(self):
        original = _set_env("GITHUB_TOKEN", "ghp-test")
        try:
            assert get_env_api_key("github-copilot") == "ghp-test"
        finally:
            _restore_env("GITHUB_TOKEN", original)

    def test_unknown_provider(self):
        assert get_env_api_key("unknown-provider") is None

    def test_not_set(self):
        original = os.environ.get("OPENAI_API_KEY")
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            assert get_env_api_key("openai") is None
        finally:
            _restore_env("OPENAI_API_KEY", original)


class TestGetEnvApiKeyTyped:
    """类型化 API 密钥获取测试"""

    def test_known_provider(self):
        original = _set_env("OPENAI_API_KEY", "sk-test")
        try:
            assert get_env_api_key_typed(KnownProvider.OPENAI) == "sk-test"
        finally:
            _restore_env("OPENAI_API_KEY", original)

    def test_string_provider(self):
        original = _set_env("OPENAI_API_KEY", "sk-test")
        try:
            assert get_env_api_key_typed("openai") == "sk-test"
        finally:
            _restore_env("OPENAI_API_KEY", original)


class TestGetAllEnvApiKeys:
    """获取所有环境变量密钥测试"""

    def test_returns_dict(self):
        result = get_all_env_api_keys()
        assert isinstance(result, dict)
        assert "openai" in result
        assert "anthropic" in result
        assert "volcengine" in result
