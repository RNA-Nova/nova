"""Policy 求值、聚合与允许前缀视图的公开行为测试。"""

from nova_exec_policy.policy import Decision, Policy, parse_policy


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
