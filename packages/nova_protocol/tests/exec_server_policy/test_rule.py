"""网络规则 host 归一与协议值解析的边界测试。"""

import pytest
from nova_protocol.exec_server_policy import (
    NetworkRuleProtocol,
    normalize_network_rule_host,
)


def test_normalize_host():
    assert normalize_network_rule_host(" Example.COM. ") == "example.com"
    assert normalize_network_rule_host("[::1]:8080") == "::1"
    with pytest.raises(ValueError):
        normalize_network_rule_host("https://example.com")
    with pytest.raises(ValueError):
        normalize_network_rule_host("*.example.com")
    with pytest.raises(ValueError):
        normalize_network_rule_host("")


def test_normalize_host_port_must_be_ascii_digits():
    """端口判定只认 ASCII 数字（对位 codex `is_ascii_digit`——`str.isdigit()`
    收非 ASCII 数字的旧行为会让 py 剥落端口、codex 保留整串，双端分歧）"""
    # 阿拉伯-印度数字"٤٤٣"不是合法端口：host:port 整串保留为 host（不剥落）
    assert normalize_network_rule_host("example.com:٤٤٣") == "example.com:٤٤٣"
    # 上标数字同理不剥落
    assert normalize_network_rule_host("example.com:⁴⁴³") == "example.com:⁴⁴³"
    # bracket 形态的非 ASCII 端口 → unsupported suffix（与 codex 一致报错）
    with pytest.raises(ValueError, match="unsupported suffix"):
        normalize_network_rule_host("[::1]:٨٠٨٠")
    # ASCII 端口照常剥落
    assert normalize_network_rule_host("example.com:443") == "example.com"
    assert normalize_network_rule_host("[::1]:8080") == "::1"


def test_normalize_host_lowercases_ascii_only():
    """小写化对位 codex `to_ascii_lowercase`（只折 A–Z）——`str.lower()` 的
    Unicode 全量小写化会把 İ 折成 "i̇" 两码点，双端归一结果分叉"""
    # 土耳其大写 İ（U+0130）：codex 原样保留，不折
    assert normalize_network_rule_host("İExample.COM") == "İexample.com"
    # 全角等非 ASCII 大写同样不折
    assert normalize_network_rule_host("ＡＢＣ.com") == "ＡＢＣ.com"


def test_normalize_host_whitespace_uses_rust_char_table():
    """空白判定对位 Rust `char::is_whitespace`（Unicode White_Space 属性）——
    `str.isspace()` 是其超集（多收 U+001C–U+001F），旧行为把 codex 收的
    host 拒掉、还把 codex 不裁的尾部分隔符裁掉，双端分歧"""
    # U+001F 不是 White_Space：codex 不收不裁、整串保留为合法 host
    assert normalize_network_rule_host("exa\x1fmple.com") == "exa\x1fmple.com"
    assert normalize_network_rule_host("example.com\x1f") == "example.com\x1f"
    # White_Space 表内字符（如 NBSP U+00A0）：两端一致拒绝
    with pytest.raises(ValueError, match="whitespace"):
        normalize_network_rule_host("exa example.com")
    # 表内字符在首尾照常裁剪（TAB 属 White_Space）
    assert normalize_network_rule_host("\texample.com\n") == "example.com"


def test_network_rule_protocol_parse_error_names_the_value():
    """协议解析错误文案带原始值尾巴（对位 codex rule.rs 的 " (got {other})"）"""
    with pytest.raises(ValueError, match=r"\(got gopher\)"):
        NetworkRuleProtocol.parse("gopher")
