"""
配置值解析模块测试。
"""

import os
from unittest.mock import patch

import pytest

from nova_harness.core.config.resolve import (
    resolve_config_value,
    resolve_headers,
)
from tests._helpers.resolve import clear_command_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_command_cache()
    yield
    clear_command_cache()


def test_resolve_config_value_returns_env_variable():
    """配置值与同名环境变量存在时，应返回环境变量值。"""
    with patch.dict(os.environ, {"MY_SECRET": "secret-value"}):
        assert resolve_config_value("MY_SECRET") == "secret-value"


def test_resolve_config_value_returns_literal_when_no_env():
    """没有同名环境变量时，应返回原字符串。"""
    key = "NON_EXISTENT_ENV_VAR_LITERAL"
    assert resolve_config_value(key) == key


def test_resolve_command_value_executes_shell():
    """以 ! 开头的配置值应作为 shell 命令执行并返回输出。"""
    assert resolve_config_value("!echo hello-world") == "hello-world"


def test_resolve_command_value_caches_result():
    """shell 命令结果应被缓存。"""
    with patch(
        "nova_harness.core.config.resolve.subprocess.check_output",
        return_value="first\n",
    ) as mock_subprocess:
        assert resolve_config_value("!echo cache-me") == "first"
        assert resolve_config_value("!echo cache-me") == "first"
        mock_subprocess.assert_called_once()


def test_resolve_command_failure_returns_none():
    """shell 命令执行失败应返回 None。"""
    import subprocess as sp

    with patch(
        "nova_harness.core.config.resolve.subprocess.check_output",
        side_effect=sp.CalledProcessError(1, "false"),
    ):
        assert resolve_config_value("!false") is None


def test_resolve_headers_resolves_values():
    """resolve_headers 应对每个 header 值调用解析逻辑。"""
    with patch.dict(os.environ, {"API_KEY": "key123"}):
        headers = {"Authorization": "API_KEY", "X-Custom": "literal"}
        resolved = resolve_headers(headers)
        assert resolved == {"Authorization": "key123", "X-Custom": "literal"}


def test_resolve_headers_empty_returns_none():
    """空字典或 None 应返回 None。"""
    assert resolve_headers(None) is None
    assert resolve_headers({}) is None


def test_resolve_headers_skips_none_values():
    """解析后值为 None 的键应被跳过。"""
    with patch(
        "nova_harness.core.config.resolve.resolve_config_value", return_value=None
    ):
        assert resolve_headers({"X": "value"}) is None
