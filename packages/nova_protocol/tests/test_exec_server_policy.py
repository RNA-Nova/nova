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


def test_check_empty_policy_allows():
    assert Policy.empty().check(("anything",)).decision is Decision.ALLOW


def test_check_multiple_aggregates():
    policy = parse_policy("t.rules", 'prefix_rule(pattern=["rm"], decision="prompt")')
    evaluation = policy.check_multiple([("ls",), ("rm", "-rf", "/")])
    assert evaluation.decision is Decision.PROMPT


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


# ── amend 文本 ──


def test_format_prefix_rule():
    assert (
        format_prefix_rule(("echo", "Hello, world!"))
        == 'prefix_rule(pattern=["echo", "Hello, world!"], decision="allow")'
    )
    with pytest.raises(ValueError):
        format_prefix_rule(())


def test_format_network_rule():
    assert (
        format_network_rule(
            "Example.com", NetworkRuleProtocol.HTTPS, Decision.FORBIDDEN
        )
        == 'network_rule(host="example.com", protocol="https", decision="forbidden")'
    )


def test_amend_text_roundtrips_through_parser():
    line = format_prefix_rule(("git", "push"))
    policy = parse_policy("t.rules", line)
    assert policy.check(("git", "push", "origin")).decision is Decision.ALLOW
    line = format_network_rule("example.com", NetworkRuleProtocol.HTTP, Decision.ALLOW)
    policy = parse_policy("t.rules", line)
    assert policy.network_rules[0].host == "example.com"


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
