"""策略解析器的关键字边界回归测试。"""

import pytest
from nova_exec_policy.policy import (
    Decision,
    NetworkRuleProtocol,
    PolicyParseError,
    parse_policy,
)


def test_duplicate_rule_keyword_is_rejected():
    with pytest.raises(PolicyParseError, match="duplicate"):
        parse_policy(
            "duplicate.rules",
            'prefix_rule(pattern=["git"], decision="allow", decision="forbidden")',
        )


def test_keyword_unpacking_is_rejected_with_domain_error():
    with pytest.raises(PolicyParseError, match="unpack"):
        parse_policy(
            "unpack.rules",
            'prefix_rule(pattern=["git"], **{"decision": "allow"})',
        )


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
