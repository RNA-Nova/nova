"""tools_common/shell.py 单元测试：输出清洗 + shell 解析。"""

import os

import pytest
from nova_coding_agent.tools_common.shell import (
    _bash_shell_config,
    get_shell_config,
    sanitize_binary_output,
    sanitize_shell_output,
    strip_ansi,
)


def test_strip_ansi_removes_csi_sequences():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"
    assert strip_ansi("\x1b[1;32mbold green\x1b[0m!") == "bold green!"


def test_strip_ansi_removes_osc_sequences():
    # OSC 标题序列：ESC ] 0 ; title BEL
    assert strip_ansi("\x1b]0;window title\x07visible") == "visible"


def test_strip_ansi_passthrough_plain_text():
    assert strip_ansi("plain text") == "plain text"


def test_sanitize_binary_output():
    # C0 控制字符被过滤；\t \n \r 保留
    assert sanitize_binary_output("a\x00b\x07c\td\ne\rf") == "abc\td\ne\rf"
    # Unicode 格式字符（0xFFF9-0xFFFB）被过滤
    assert sanitize_binary_output("x\ufff9y\ufffbz") == "xyz"


def test_sanitize_shell_output_full_pipeline():
    # strip ANSI + 消毒 + \r 归一
    assert sanitize_shell_output("\x1b[31merr\x1b[0m\r\n\x00done") == "err\ndone"


# ---------------------------------------------------------------------------
# get_shell_config
# ---------------------------------------------------------------------------


def test_get_shell_config_custom_path():
    cfg = get_shell_config("/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh")
    assert cfg.args == ["-c"]
    assert cfg.command_transport == "argv"


def test_get_shell_config_custom_path_not_found():
    with pytest.raises(FileNotFoundError, match="Custom shell path not found"):
        get_shell_config("/nonexistent/bash-xyz")


def test_get_shell_config_default_resolves():
    cfg = get_shell_config()
    assert cfg.shell  # /bin/bash、PATH 上的 bash 或 sh 兜底
    assert cfg.args == ["-c"]
    assert cfg.command_transport == "argv"


def test_legacy_wsl_bash_path_uses_stdin_transport():
    cfg = _bash_shell_config(r"C:\Windows\System32\bash.exe")
    assert cfg.command_transport == "stdin"
    assert cfg.args == ["-s"]


def test_non_wsl_path_uses_argv_transport():
    cfg = _bash_shell_config("/usr/local/bin/bash")
    assert cfg.command_transport == "argv"
    assert cfg.args == ["-c"]
