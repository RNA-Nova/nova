"""规则文本 amend 写出与解析互认测试。"""

import pytest
from nova_protocol.exec_server_policy import (
    Decision,
    NetworkRuleProtocol,
    format_network_rule,
    format_prefix_rule,
    parse_policy,
)


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
