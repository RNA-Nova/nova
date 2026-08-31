"""
配置值解析模块测试（对齐 TS resolve-config-value.ts 语义）。

- ``$VAR`` / ``${VAR}`` 环境变量插值
- ``$$`` / ``$!`` 转义
- ``!cmd`` shell 命令（带缓存）
- 裸字符串按字面量处理
"""

import os
from unittest.mock import patch

import pytest

from nova_harness.core.config.resolve import (
    get_config_value_env_var_name,
    get_config_value_env_var_names,
    get_missing_config_value_env_var_names,
    is_command_config_value,
    is_config_value_configured,
    resolve_config_value,
    resolve_config_value_or_throw,
    resolve_config_value_uncached,
    resolve_headers,
    resolve_headers_or_throw,
)
from tests._helpers.resolve import clear_command_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_command_cache()
    yield
    clear_command_cache()


# ---------------------------------------------------------------------------
# 字面量与 env 插值
# ---------------------------------------------------------------------------


def test_bare_string_is_literal_even_when_env_exists():
    """裸字符串一律按字面量处理，不再隐式查找同名环境变量。"""
    with patch.dict(os.environ, {"MY_SECRET": "secret-value"}):
        assert resolve_config_value("MY_SECRET") == "MY_SECRET"


def test_dollar_prefixed_env_var_resolves():
    with patch.dict(os.environ, {"MY_SECRET": "secret-value"}):
        assert resolve_config_value("$MY_SECRET") == "secret-value"


def test_braced_env_var_resolves():
    with patch.dict(os.environ, {"MY_SECRET": "secret-value"}):
        assert resolve_config_value("${MY_SECRET}") == "secret-value"


def test_missing_env_var_returns_none():
    assert resolve_config_value("$NOVA_TEST_DEFINITELY_MISSING") is None


def test_mixed_template_interpolation():
    with patch.dict(os.environ, {"HOST": "example.com", "PORT": "8080"}):
        assert (
            resolve_config_value("https://${HOST}:$PORT/v1")
            == "https://example.com:8080/v1"
        )


def test_mixed_template_missing_one_returns_none():
    with patch.dict(os.environ, {"HOST": "example.com"}):
        assert resolve_config_value("https://${HOST}:$NOVA_MISSING_PORT/v1") is None


def test_invalid_brace_name_is_literal():
    """${} 内不是合法变量名时按字面量保留。"""
    assert resolve_config_value("${not-a-var}") == "${not-a-var}"


def test_unclosed_brace_is_literal():
    assert resolve_config_value("${UNCLOSED") == "${UNCLOSED"


def test_dollar_without_name_is_literal():
    assert resolve_config_value("price is $") == "price is $"


# ---------------------------------------------------------------------------
# 转义
# ---------------------------------------------------------------------------


def test_double_dollar_escapes_to_literal_dollar():
    assert resolve_config_value("$$FOO") == "$FOO"


def test_dollar_bang_escapes_to_literal_bang():
    assert resolve_config_value("$!echo hi") == "!echo hi"


def test_escaped_parts_mix_with_env():
    with patch.dict(os.environ, {"NAME": "nova"}):
        assert resolve_config_value("$$NAME is $NAME") == "$NAME is nova"


# ---------------------------------------------------------------------------
# 命令型
# ---------------------------------------------------------------------------


def test_command_value_executes_shell():
    assert resolve_config_value("!echo hello-world") == "hello-world"


def test_command_value_caches_result():
    with patch(
        "nova_harness.core.config.resolve.subprocess.check_output",
        return_value="first\n",
    ) as mock_subprocess:
        assert resolve_config_value("!echo cache-me") == "first"
        assert resolve_config_value("!echo cache-me") == "first"
        mock_subprocess.assert_called_once()


def test_command_uncached_bypasses_cache():
    with patch(
        "nova_harness.core.config.resolve.subprocess.check_output",
        side_effect=["a\n", "b\n"],
    ) as mock_subprocess:
        assert resolve_config_value_uncached("!echo x") == "a"
        assert resolve_config_value_uncached("!echo x") == "b"
        assert mock_subprocess.call_count == 2


def test_command_failure_returns_none():
    import subprocess as sp

    with patch(
        "nova_harness.core.config.resolve.subprocess.check_output",
        side_effect=sp.CalledProcessError(1, "false"),
    ):
        assert resolve_config_value("!false") is None


def test_command_empty_output_returns_none():
    with patch(
        "nova_harness.core.config.resolve.subprocess.check_output",
        return_value="  \n",
    ):
        assert resolve_config_value("!echo -n ''") is None


# ---------------------------------------------------------------------------
# 内省辅助
# ---------------------------------------------------------------------------


def test_get_config_value_env_var_name_single_ref():
    assert get_config_value_env_var_name("$FOO") == "FOO"
    assert get_config_value_env_var_name("${FOO}") == "FOO"
    assert get_config_value_env_var_name("prefix-$FOO") is None
    assert get_config_value_env_var_name("literal") is None
    assert get_config_value_env_var_name("!cmd") is None


def test_get_config_value_env_var_names_deduped():
    assert get_config_value_env_var_names("$A-${B}-$A") == ["A", "B"]
    assert get_config_value_env_var_names("literal") == []


def test_get_missing_env_var_names():
    with patch.dict(os.environ, {"PRESENT": "1"}):
        missing = get_missing_config_value_env_var_names("$PRESENT-$ABSENT_X")
        assert missing == ["ABSENT_X"]


def test_is_command_config_value():
    assert is_command_config_value("!echo hi")
    assert not is_command_config_value("$!echo hi")
    assert not is_command_config_value("$FOO")


def test_is_config_value_configured():
    with patch.dict(os.environ, {"PRESENT": "1"}):
        assert is_config_value_configured("$PRESENT")
        assert is_config_value_configured("literal")
        assert not is_config_value_configured("$ABSENT_X")


def test_explicit_env_dict_overrides_process_env():
    with patch.dict(os.environ, {"FOO": "process"}):
        assert resolve_config_value("$FOO", env={"FOO": "explicit"}) == "explicit"


# ---------------------------------------------------------------------------
# or_throw 变体
# ---------------------------------------------------------------------------


def test_or_throw_returns_resolved_value():
    with patch.dict(os.environ, {"FOO": "bar"}):
        assert resolve_config_value_or_throw("$FOO", "test key") == "bar"


def test_or_throw_names_single_missing_var():
    with pytest.raises(ValueError, match="environment variable: ABSENT_X"):
        resolve_config_value_or_throw("$ABSENT_X", "test key")


def test_or_throw_names_multiple_missing_vars():
    with pytest.raises(ValueError, match="ABSENT_A, ABSENT_B"):
        resolve_config_value_or_throw("$ABSENT_A-$ABSENT_B", "test key")


def test_or_throw_command_failure_mentions_command():
    import subprocess as sp

    with patch(
        "nova_harness.core.config.resolve.subprocess.check_output",
        side_effect=sp.CalledProcessError(1, "false"),
    ):
        with pytest.raises(ValueError, match="shell command: false"):
            resolve_config_value_or_throw("!false", "test key")


# ---------------------------------------------------------------------------
# headers
# ---------------------------------------------------------------------------


def test_resolve_headers_literal_and_env():
    with patch.dict(os.environ, {"API_KEY": "key123"}):
        headers = {"Authorization": "Bearer $API_KEY", "X-Custom": "literal"}
        resolved = resolve_headers(headers)
        assert resolved == {"Authorization": "Bearer key123", "X-Custom": "literal"}


def test_resolve_headers_empty_returns_none():
    assert resolve_headers(None) is None
    assert resolve_headers({}) is None


def test_resolve_headers_skips_unresolvable_values():
    assert resolve_headers({"X": "$NOVA_MISSING_HEADER"}) is None


def test_resolve_headers_or_throw_raises_with_header_name():
    with pytest.raises(ValueError, match='header "X-Team"'):
        resolve_headers_or_throw({"X-Team": "$NOVA_MISSING_HEADER"}, "test provider")
