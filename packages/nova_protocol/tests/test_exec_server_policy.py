"""exec_server_policy 测试——execpolicy 引擎 + denial 启发式（金标对位）"""

import pytest
from nova_protocol.exec_server_policy import (
    Decision,
    Evaluation,
    NetworkRuleProtocol,
    Policy,
    PolicyParseError,
    format_network_rule,
    format_prefix_rule,
    is_likely_executor_managed_sandbox_denied,
    is_likely_sandbox_denied,
    normalize_network_rule_host,
    parse_policy,
)

# ── 解析器 ──


def test_parse_prefix_rules():
    policy = parse_policy(
        "test.rules",
        'prefix_rule(pattern=["git"], decision="prompt")\n'
        'prefix_rule(pattern=["git", "status"], decision="allow")\n'
        'prefix_rule(pattern=["ls"])\n',
    )
    assert len(policy.rules_by_program["git"]) == 2
    assert policy.rules_by_program["ls"][0].decision is Decision.ALLOW


def test_parse_pattern_alternatives():
    policy = parse_policy(
        "test.rules",
        'prefix_rule(pattern=["git", ["push", "fetch"]], decision="prompt")',
    )
    evaluation = policy.check(("git", "fetch", "origin"))
    assert evaluation.decision is Decision.PROMPT


def test_parse_rejects_non_literal_and_unknown():
    with pytest.raises(PolicyParseError):
        parse_policy("t.rules", 'prefix_rule(pattern=["ls" if True else "ls"])')
    with pytest.raises(PolicyParseError):
        parse_policy("t.rules", 'prefix_rule(pattern=["ls"], bogus=1)')
    with pytest.raises(PolicyParseError):
        parse_policy("t.rules", "x = 1")
    with pytest.raises(PolicyParseError):
        parse_policy("t.rules", 'prefix_rule(pattern=["ls"], decision="maybe")')


def test_match_examples_validated_at_parse():
    with pytest.raises(PolicyParseError, match="does not match"):
        parse_policy(
            "t.rules",
            'prefix_rule(pattern=["git", "push"], match=[["git", "fetch"]])',
        )
    policy = parse_policy(
        "t.rules",
        'prefix_rule(pattern=["git", "push"], match=[["git", "push", "origin"]], '
        'not_match=[["git", "fetch"]])',
    )
    assert len(policy.rules_by_program["git"]) == 1


def test_parse_network_rule_with_host_normalization():
    policy = parse_policy(
        "t.rules",
        'network_rule(host="Example.COM:443", protocol="https", decision="deny")',
    )
    rule = policy.network_rules[0]
    assert rule.host == "example.com"
    assert rule.protocol is NetworkRuleProtocol.HTTPS
    assert rule.decision is Decision.FORBIDDEN


# ── 求值 ──


def test_check_aggregates_max_decision():
    policy = parse_policy(
        "t.rules",
        'prefix_rule(pattern=["git"], decision="allow")\n'
        'prefix_rule(pattern=["git", "push"], decision="forbidden")\n',
    )
    assert policy.check(("git", "status")).decision is Decision.ALLOW
    assert policy.check(("git", "push", "origin")).decision is Decision.FORBIDDEN


def test_check_heuristics_fallback_only_when_no_rule():
    policy = parse_policy("t.rules", 'prefix_rule(pattern=["ls"], decision="allow")')
    fallback = lambda cmd: Decision.FORBIDDEN
    # 有规则命中 → fallback 不参与
    assert policy.check(("ls",), fallback).decision is Decision.ALLOW
    # 无规则命中 → fallback 生效
    evaluation = policy.check(("rm", "-rf", "/"), fallback)
    assert evaluation.decision is Decision.FORBIDDEN
    assert evaluation.is_match() is False


def test_check_without_match_or_fallback_has_no_decision():
    """无命中且无兜底 → decision=None（对位 codex from_matches 非空不变式：
    codex 的 check 强制携带兜底，永不空命中；无兜底调用方只读
    matched_rules——不凭空造 ALLOW 架空裁决）"""
    evaluation = Policy.empty().check(("anything",))
    assert evaluation.decision is None
    assert evaluation.matched_rules == ()
    assert evaluation.is_match() is False
    # check_multiple 同纪律：全部段无命中且无兜底 → None
    assert Policy.empty().check_multiple([("ls",), ("rm",)]).decision is None
    # 兜底返回 None 视为无兜底命中 → None
    assert Policy.empty().check(("ls",), lambda cmd: None).decision is None


def test_check_multiple_aggregates():
    policy = parse_policy("t.rules", 'prefix_rule(pattern=["rm"], decision="prompt")')
    evaluation = policy.check_multiple([("ls",), ("rm", "-rf", "/")])
    assert evaluation.decision is Decision.PROMPT


# ── get_allowed_prefixes（对位 policy.rs 同名 + render_pattern_token）──


def test_get_allowed_prefixes_renders_multi_choice_tokens():
    """多选 token 渲染为 "[a|b]" 单 token（render_pattern_token——不丢信息；
    旧实现取 alternatives[0] 静默丢多选）"""
    policy = parse_policy(
        "t.rules",
        'prefix_rule(pattern=["git", ["push", "fetch"], "--force"], decision="allow")',
    )
    assert policy.get_allowed_prefixes() == [("git", "[push|fetch]", "--force")]


def test_get_allowed_prefixes_allow_only_sorted_deduped():
    """只收 allow 规则；输出排序 + 去重（对位 codex 的 sort()+dedup()）"""
    policy = parse_policy(
        "t.rules",
        'prefix_rule(pattern=["git", "status"], decision="allow")\n'
        'prefix_rule(pattern=["git", "push"], decision="forbidden")\n'
        'prefix_rule(pattern=["ls"], decision="prompt")\n'
        'prefix_rule(pattern=["cargo", "build"], decision="allow")\n'
        # 同前缀重复规则 → 去重
        'prefix_rule(pattern=["git", "status"], decision="allow")\n',
    )
    assert policy.get_allowed_prefixes() == [
        ("cargo", "build"),
        ("git", "status"),
    ]


def test_get_allowed_prefixes_empty_policy():
    assert Policy.empty().get_allowed_prefixes() == []


# ── host 归一 ──


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


# ── amend 文本 ──


def test_format_prefix_rule():
    assert (
        format_prefix_rule(("echo", "Hello, world!"))
        == 'prefix_rule(pattern=["echo", "Hello, world!"], decision="allow")'
    )
    with pytest.raises(ValueError):
        format_prefix_rule(())


def test_format_network_rule():
    """Forbidden 的写盘文本对位 codex amend.rs writer：`decision="deny"`
    （不是 decision.value 的 "forbidden"；解析侧 "deny" 读回 Forbidden，
    写读互认）"""
    assert (
        format_network_rule(
            "Example.com", NetworkRuleProtocol.HTTPS, Decision.FORBIDDEN
        )
        == 'network_rule(host="example.com", protocol="https", decision="deny")'
    )


def test_format_network_rule_rejects_empty_justification():
    """justification 空串/纯空白即拒（对位 codex amend.rs 守卫——否则写出的
    行自己 parse 不回来：NetworkRule 校验拒绝空 justification）"""
    for blank in ("", "   "):
        with pytest.raises(ValueError, match="justification cannot be empty"):
            format_network_rule(
                "example.com",
                NetworkRuleProtocol.HTTPS,
                Decision.ALLOW,
                justification=blank,
            )


def test_amend_text_roundtrips_through_parser():
    line = format_prefix_rule(("git", "push"))
    policy = parse_policy("t.rules", line)
    assert policy.check(("git", "push", "origin")).decision is Decision.ALLOW
    line = format_network_rule("example.com", NetworkRuleProtocol.HTTP, Decision.ALLOW)
    policy = parse_policy("t.rules", line)
    assert policy.network_rules[0].host == "example.com"
    # Forbidden → "deny" 写盘后 parse 回 Forbidden（对位 amend.rs 与
    # parse_network_rule_decision 的写读互认）
    line = format_network_rule(
        "blocked.example.com",
        NetworkRuleProtocol.HTTPS,
        Decision.FORBIDDEN,
        justification="known-bad",
    )
    policy = parse_policy("t.rules", line)
    rule = policy.network_rules[0]
    assert rule.decision is Decision.FORBIDDEN
    assert rule.justification == "known-bad"


# ── denial 启发式 ──


def test_executor_managed_denied_keywords():
    assert (
        is_likely_executor_managed_sandbox_denied(1, "", "Operation not permitted")
        is True
    )
    assert is_likely_executor_managed_sandbox_denied(1, "", "file not found") is False
    assert (
        is_likely_executor_managed_sandbox_denied(0, "", "Operation not permitted")
        is False
    )


def test_sandbox_denied_heuristic():
    # 未沙箱 → 否
    assert is_likely_sandbox_denied(None, 1, "", "Operation not permitted") is False
    # 零退出码 → 否
    assert is_likely_sandbox_denied("linux_seccomp", 0, "", "x") is False
    # 快速拒绝码 → 否（即便含关键词也先被关键词判真——关键词优先）
    assert is_likely_sandbox_denied("seatbelt", 126, "", "Permission denied") is True
    assert (
        is_likely_sandbox_denied("seatbelt", 126, "", "zsh: command not found") is False
    )
    # seccomp SIGSYS → 真
    assert is_likely_sandbox_denied("linux_seccomp", 159, "", "") is True
    assert is_likely_sandbox_denied("seatbelt", 159, "", "") is False
