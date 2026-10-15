"""execpolicy 金标对跑——codex execpolicy tests/basic.rs（27 例）全量移植。

金标参照：codex 仓 execpolicy（锚点 39f4277087——`parser.rs`/`policy.rs`/
`rule.rs`/`amend.rs`/`executable_name.rs` + `tests/basic.rs`；更早的 nova 仓
Rust 存档已删除——对跑时 cargo test 27/27 绿）。本文件即活语义参照：py 引擎
（`nova_protocol/exec_server_policy/` 包，模块边界对位 codex）与 Rust 在
设计面内决策全等。

矩阵映射（27 个 Rust 测试 → 本文件）：

- 行为完全一致 → EVAL_CASES / PARSE_ERROR_CASES 参数化 + 规则快照/部件测试：
  basic_match、justification_is_attached_to_forbidden_matches、
  justification_can_be_used_with_allow_decision、justification_cannot_be_empty、
  add_prefix_rule_extends_policy（API 适配：py add_prefix_rule 收 PrefixRule 对象）、
  add_prefix_rule_rejects_empty_prefix（分层等价：parse 层 + format 层）、
  parses_multiple_policy_files、only_first_token_alias_expands_to_multiple_rules、
  tail_aliases_are_not_cartesian_expanded、
  match_and_not_match_examples_are_enforced（list 形式 + 字符串形式全案）、
  strictest_decision_wins_across_matches、strictest_decision_across_multiple_commands、
  heuristics_match_is_returned_when_no_policy_matches、
  network_rules_compile_into_domain_lists（解析 + 编译全案）、
  network_rule_rejects_wildcard_hosts、
  append_allow_prefix_rule_dedupes_existing_rule（文本 + 追加纯函数全案；
  落盘去重归 harness RulesStore，实证见 nova_harness test_rules_store.py）、
  parses_host_executable_paths、host_executable_rejects_non_absolute_path、
  host_executable_rejects_name_with_path_separator、
  host_executable_rejects_path_with_wrong_basename、
  host_executable_last_definition_wins、
  host_executable_resolution_uses_basename_rule_when_allowed、
  prefix_rule_examples_honor_host_executable_resolution、
  host_executable_resolution_respects_explicit_empty_allowlist、
  host_executable_resolution_ignores_path_not_in_allowlist、
  host_executable_resolution_falls_back_without_mapping、
  host_executable_resolution_does_not_override_exact_match。

- 已闭环分歧（挂账记录——实现位置见各条；对位测试已迁移转正，全文件不再
  保留 xfail 标记）：
  D1  【已闭环】首 token 多选展开：parser.py 按 `first_token.alternatives()`
      展开为多规则（first=备选、rest=剩余 tokens；仅首 token 展开，尾部不
      笛卡尔展开）——test_only_first_token_alias_expands_to_multiple_rules。
  D2  【已闭环】字符串形式 match/not_match 样例：parser.py 经 shlex 切词
      （对位 parser.rs `shlex::split`）——
      test_match_and_not_match_examples_accept_string_form。
  D3  【已闭环】host_executable 声明族：parser.py + executable_name.py
      （裸名校验/绝对路径/basename 校验/重复路径去重/后定义覆盖）——
      test_parses_host_executable_paths 等五例。
  D4  【已闭环】check_with_options(resolve_host_executables) 求值族：
      policy.py MatchOptions（默认不解析）/match_host_executable_rules/
      resolved_program——test_host_executable_resolution_* 五例 +
      test_prefix_rule_examples_honor_host_executable_resolution。
  D5  【已闭环】compiled_network_domains 域名单编译（allow/deny upsert
      语义）——test_network_rules_compile_into_domain_lists。
  D6  【已闭环】命中负载：justification 随命中克隆、resolved_program 随
      host 解析命中（对位 rule.rs RuleMatch 四字段形状）——
      test_justification_on_match_payload / test_match_payload_shape。
  D7  【已闭环】blocking_append_allow_prefix_rule 分层：文本语义（精确行
      去重 + 补换行纪律）归 exec_server_policy/amend.py::append_rule_line_text
      ——test_append_rule_line_text_dedupes_and_newline_discipline；咨询式
      文件锁与落盘 I/O 归消费方 harness RulesStore.append_amendment（分层
      纪律：引擎纯、I/O 归消费方；文件行为实证见 nova_harness
      tests/core/harness/test_rules_store.py）。
  D8  【已闭环】精确路径规则查找键：check 以字面 cmd[0] 作查找键
      （Rust 默认语义）——test_exact_path_rule_matches_literal_key。
"""

from dataclasses import fields

import pytest
from nova_protocol.exec_server_policy import (
    Decision,
    Evaluation,
    HeuristicsRuleMatch,
    MatchOptions,
    NetworkRuleProtocol,
    PatternToken,
    Policy,
    PolicyParseError,
    PrefixPattern,
    PrefixRule,
    PrefixRuleMatch,
    append_rule_line_text,
    format_prefix_rule,
    parse_policy,
)

# =============================================================================
# 对跑辅助
# =============================================================================


def allow_all(_cmd) -> Decision:
    """对位 basic.rs allow_all"""
    return Decision.ALLOW


def prompt_all(_cmd) -> Decision:
    """对位 basic.rs prompt_all"""
    return Decision.PROMPT


def parse_all(sources) -> Policy:
    """多规则文件按序合并（对位 PolicyParser 多次 parse + build 的聚合语义——
    rules 追加、network_rules 追加、host_executables 后定义覆盖先定义）"""
    merged = Policy.empty()
    for identifier, contents in sources:
        policy = parse_policy(identifier, contents)
        for rules in policy.rules_by_program.values():
            for rule in rules:
                merged.add_prefix_rule(rule)
        merged.network_rules.extend(policy.network_rules)
        for name, paths in policy.host_executables().items():
            merged.set_host_executable_paths(name, paths)
    return merged


def prm(prefix, decision, resolved_program=None, justification=None) -> PrefixRuleMatch:
    return PrefixRuleMatch(
        matched_prefix=tuple(prefix),
        decision=decision,
        resolved_program=resolved_program,
        justification=justification,
    )


def hrm(decision, command=None) -> HeuristicsRuleMatch:
    return HeuristicsRuleMatch(decision=decision, command=command)


#: 对位 basic.rs 的 `MatchOptions { resolve_host_executables: true }`
RESOLVE_HOST_EXECUTABLES = MatchOptions(resolve_host_executables=True)

# =============================================================================
# 规则源（逐字对位 basic.rs；starlark 关键字调用语法与 Python 兼容）
# =============================================================================

S_BASIC = """
prefix_rule(
    pattern = ["git", "status"],
)
"""

S_RM_FORBIDDEN = """
prefix_rule(
    pattern = ["rm"],
    decision = "forbidden",
    justification = "destructive command",
)
"""

S_LS_ALLOW_JUST = """
prefix_rule(
    pattern = ["ls"],
    decision = "allow",
    justification = "safe and commonly used",
)
"""

S_LS_PROMPT_JUST_EMPTY = """
prefix_rule(
    pattern = ["ls"],
    decision = "prompt",
    justification = "   ",
)
"""

S_GIT_PROMPT = """
prefix_rule(
    pattern = ["git"],
    decision = "prompt",
)
"""

S_GIT_COMMIT_FORBIDDEN = """
prefix_rule(
    pattern = ["git", "commit"],
    decision = "forbidden",
)
"""

S_TWO_RULES = S_GIT_PROMPT + S_GIT_COMMIT_FORBIDDEN

S_ALIAS_FIRST_TOKEN = """
prefix_rule(
    pattern = [["bash", "sh"], ["-c", "-l"]],
)
"""

S_NPM_ALIASES = """
prefix_rule(
    pattern = ["npm", ["i", "install"], ["--legacy-peer-deps", "--no-save"]],
)
"""

#: basic.rs 原样源（字符串形式样例与列表形式混写——对位 parse_examples）
S_EXAMPLES_RUST = """
prefix_rule(
    pattern = ["git", "status"],
    match = [["git", "status"], "git status"],
    not_match = [
        ["git", "--config", "color.status=always", "status"],
        "git --config color.status=always status",
    ],
)
"""

#: 同一案例的 list 形式（求值矩阵用）
S_EXAMPLES_LIST_ONLY = """
prefix_rule(
    pattern = ["git", "status"],
    match = [["git", "status"]],
    not_match = [
        ["git", "--config", "color.status=always", "status"],
    ],
)
"""

S_NETWORK = """
network_rule(host = "google.com", protocol = "http", decision = "allow")
network_rule(host = "api.github.com", protocol = "https", decision = "allow")
network_rule(host = "blocked.example.com", protocol = "https", decision = "deny")
network_rule(host = "prompt-only.example.com", protocol = "https", decision = "prompt")
"""

#: host_executable 族规则源（mac/linux 主机路径形态——对位 host_absolute_path）
S_HOST_EXEC_DEDUP = """
host_executable(
    name = "git",
    paths = [
        "/opt/homebrew/bin/git",
        "/usr/bin/git",
        "/usr/bin/git",
    ],
)
"""

S_HOST_EXEC_BASENAME_RULE = """
prefix_rule(pattern = ["git", "status"], decision = "prompt")
host_executable(name = "git", paths = ["/usr/bin/git"])
"""

S_HOST_EXEC_EXAMPLES = """
prefix_rule(
    pattern = ["git", "status"],
    match = [["/usr/bin/git", "status"]],
    not_match = [["/opt/homebrew/bin/git", "status"]],
)
host_executable(name = "git", paths = ["/usr/bin/git"])
"""

S_HOST_EXEC_EMPTY_ALLOWLIST = """
prefix_rule(pattern = ["git"], decision = "prompt")
host_executable(name = "git", paths = [])
"""

S_HOST_EXEC_ALLOWLIST = """
prefix_rule(pattern = ["git"], decision = "prompt")
host_executable(name = "git", paths = ["/usr/bin/git"])
"""

S_GIT_PROMPT_ONLY = """
prefix_rule(pattern = ["git"], decision = "prompt")
"""

S_HOST_EXEC_EXACT_MATCH = """
prefix_rule(pattern = ["/usr/bin/git"], decision = "allow")
prefix_rule(pattern = ["git"], decision = "prompt")
host_executable(name = "git", paths = ["/usr/bin/git"])
"""


# =============================================================================
# 金标求值矩阵（与 Rust 完全一致的部分）
# =============================================================================

EVAL_CASES = [
    # basic_match
    pytest.param(
        [("test.rules", S_BASIC)],
        [("git", "status")],
        allow_all,
        Evaluation(
            decision=Decision.ALLOW,
            matched_rules=(prm(("git", "status"), Decision.ALLOW),),
        ),
        id="basic_match",
    ),
    # justification_is_attached_to_forbidden_matches（决策+命中面全案——
    # justification 随命中克隆，对位 basic.rs 同名断言）
    pytest.param(
        [("test.rules", S_RM_FORBIDDEN)],
        [("rm", "-rf", "/some/important/folder")],
        allow_all,
        Evaluation(
            decision=Decision.FORBIDDEN,
            matched_rules=(
                prm(("rm",), Decision.FORBIDDEN, justification="destructive command"),
            ),
        ),
        id="justification_is_attached_to_forbidden_matches",
    ),
    # justification_can_be_used_with_allow_decision（规则命中 → 启发式不参与）
    pytest.param(
        [("test.rules", S_LS_ALLOW_JUST)],
        [("ls", "-l")],
        prompt_all,
        Evaluation(
            decision=Decision.ALLOW,
            matched_rules=(
                prm(("ls",), Decision.ALLOW, justification="safe and commonly used"),
            ),
        ),
        id="justification_can_be_used_with_allow_decision",
    ),
    # parses_multiple_policy_files（求值部分；规则快照见下方快照测试）
    pytest.param(
        [("first.rules", S_GIT_PROMPT), ("second.rules", S_GIT_COMMIT_FORBIDDEN)],
        [("git", "status")],
        allow_all,
        Evaluation(
            decision=Decision.PROMPT,
            matched_rules=(prm(("git",), Decision.PROMPT),),
        ),
        id="parses_multiple_policy_files-status",
    ),
    pytest.param(
        [("first.rules", S_GIT_PROMPT), ("second.rules", S_GIT_COMMIT_FORBIDDEN)],
        [("git", "commit", "-m", "hi")],
        allow_all,
        Evaluation(
            decision=Decision.FORBIDDEN,
            matched_rules=(
                prm(("git",), Decision.PROMPT),
                prm(("git", "commit"), Decision.FORBIDDEN),
            ),
        ),
        id="parses_multiple_policy_files-commit",
    ),
    # tail_aliases_are_not_cartesian_expanded（求值部分；规则快照见下方）
    pytest.param(
        [("test.rules", S_NPM_ALIASES)],
        [("npm", "i", "--legacy-peer-deps")],
        allow_all,
        Evaluation(
            decision=Decision.ALLOW,
            matched_rules=(prm(("npm", "i", "--legacy-peer-deps"), Decision.ALLOW),),
        ),
        id="tail_aliases_are_not_cartesian_expanded-i",
    ),
    pytest.param(
        [("test.rules", S_NPM_ALIASES)],
        [("npm", "install", "--no-save", "leftpad")],
        allow_all,
        Evaluation(
            decision=Decision.ALLOW,
            matched_rules=(prm(("npm", "install", "--no-save"), Decision.ALLOW),),
        ),
        id="tail_aliases_are_not_cartesian_expanded-install",
    ),
    # match_and_not_match_examples_are_enforced（list 形式；字符串形式见
    # test_match_and_not_match_examples_accept_string_form）
    pytest.param(
        [("test.rules", S_EXAMPLES_LIST_ONLY)],
        [("git", "status")],
        allow_all,
        Evaluation(
            decision=Decision.ALLOW,
            matched_rules=(prm(("git", "status"), Decision.ALLOW),),
        ),
        id="match_and_not_match_examples_are_enforced-match",
    ),
    pytest.param(
        [("test.rules", S_EXAMPLES_LIST_ONLY)],
        [("git", "--config", "color.status=always", "status")],
        allow_all,
        Evaluation(
            decision=Decision.ALLOW,
            matched_rules=(
                hrm(
                    Decision.ALLOW,
                    ("git", "--config", "color.status=always", "status"),
                ),
            ),
        ),
        id="match_and_not_match_examples_are_enforced-not_match",
    ),
    # strictest_decision_wins_across_matches
    pytest.param(
        [("test.rules", S_TWO_RULES)],
        [("git", "commit", "-m", "hi")],
        allow_all,
        Evaluation(
            decision=Decision.FORBIDDEN,
            matched_rules=(
                prm(("git",), Decision.PROMPT),
                prm(("git", "commit"), Decision.FORBIDDEN),
            ),
        ),
        id="strictest_decision_wins_across_matches",
    ),
    # strictest_decision_across_multiple_commands（check_multiple 聚合）
    pytest.param(
        [("test.rules", S_TWO_RULES)],
        [("git", "status"), ("git", "commit", "-m", "hi")],
        allow_all,
        Evaluation(
            decision=Decision.FORBIDDEN,
            matched_rules=(
                prm(("git",), Decision.PROMPT),
                prm(("git",), Decision.PROMPT),
                prm(("git", "commit"), Decision.FORBIDDEN),
            ),
        ),
        id="strictest_decision_across_multiple_commands",
    ),
    # heuristics_match_is_returned_when_no_policy_matches
    pytest.param(
        [],
        [("python",)],
        prompt_all,
        Evaluation(
            decision=Decision.PROMPT,
            matched_rules=(hrm(Decision.PROMPT, ("python",)),),
        ),
        id="heuristics_match_is_returned_when_no_policy_matches",
    ),
]


@pytest.mark.parametrize(
    "sources,commands,fallback,expected",
    EVAL_CASES,
)
def test_golden_eval(sources, commands, fallback, expected):
    """金标求值：相同规则源 + 相同命令(组) → 与 Rust 完全相同的 Evaluation"""
    policy = parse_all(sources)
    if len(commands) == 1:
        evaluation = policy.check(commands[0], fallback)
    else:
        evaluation = policy.check_multiple(list(commands), fallback)
    assert evaluation == expected


# =============================================================================
# 金标解析拒绝矩阵（错误信息子串对位）
# =============================================================================

PARSE_ERROR_CASES = [
    # justification_cannot_be_empty
    pytest.param(
        [("test.rules", S_LS_PROMPT_JUST_EMPTY)],
        "justification cannot be empty",
        id="justification_cannot_be_empty",
    ),
    # network_rule_rejects_wildcard_hosts
    pytest.param(
        [
            (
                "network.rules",
                'network_rule(host="*", protocol="http", decision="allow")',
            )
        ],
        "wildcards are not allowed",
        id="network_rule_rejects_wildcard_hosts",
    ),
    # add_prefix_rule_rejects_empty_prefix（parse 层等价：Rust 为
    # Error::InvalidPattern("pattern cannot be empty")）
    pytest.param(
        [("test.rules", "prefix_rule(pattern=[])")],
        "pattern must be a non-empty list",
        id="add_prefix_rule_rejects_empty_prefix-parse",
    ),
    # match_and_not_match_examples_are_enforced 派生：样例强制校验的反向两例
    pytest.param(
        [
            (
                "test.rules",
                'prefix_rule(pattern=["git", "push"], match=[["git", "fetch"]])',
            )
        ],
        "does not match",
        id="match_example_unmatched_rejected-derived",
    ),
    pytest.param(
        [("test.rules", 'prefix_rule(pattern=["git"], not_match=[["git", "status"]])')],
        "unexpectedly matches",
        id="not_match_example_matched_rejected-derived",
    ),
]


@pytest.mark.parametrize("sources,error_match", PARSE_ERROR_CASES)
def test_golden_parse_error(sources, error_match):
    with pytest.raises(PolicyParseError, match=error_match):
        parse_all(sources)


# =============================================================================
# 规则快照与部件断言（对位 basic.rs 中的结构性断言）
# =============================================================================


def test_add_prefix_rule_extends_policy():
    """对位 add_prefix_rule_extends_policy（API 适配：py 收 PrefixRule 对象）"""
    policy = Policy.empty()
    rule = PrefixRule(
        pattern=PrefixPattern(first="ls", rest=(PatternToken.single("-l"),)),
        decision=Decision.PROMPT,
    )
    policy.add_prefix_rule(rule)
    assert policy.rules_by_program["ls"] == [rule]
    evaluation = policy.check(("ls", "-l", "/some/important/folder"), allow_all)
    assert evaluation == Evaluation(
        decision=Decision.PROMPT,
        matched_rules=(prm(("ls", "-l"), Decision.PROMPT),),
    )


def test_parses_multiple_policy_files_rule_snapshot():
    """对位 parses_multiple_policy_files 的规则快照部分（按插入序）"""
    policy = parse_all(
        [("first.rules", S_GIT_PROMPT), ("second.rules", S_GIT_COMMIT_FORBIDDEN)]
    )
    assert policy.rules_by_program["git"] == [
        PrefixRule(
            pattern=PrefixPattern(first="git"),
            decision=Decision.PROMPT,
        ),
        PrefixRule(
            pattern=PrefixPattern(first="git", rest=(PatternToken.single("commit"),)),
            decision=Decision.FORBIDDEN,
        ),
    ]


def test_only_first_token_alias_expands_to_multiple_rules():
    """对位 only_first_token_alias_expands_to_multiple_rules（D1 闭环）：
    首 token 多选按备选展开为多规则；尾部多选不笛卡尔展开"""
    policy = parse_all([("test.rules", S_ALIAS_FIRST_TOKEN)])
    expected_bash = PrefixRule(
        pattern=PrefixPattern(
            first="bash",
            rest=(PatternToken(alternatives=("-c", "-l")),),
        ),
        decision=Decision.ALLOW,
    )
    expected_sh = PrefixRule(
        pattern=PrefixPattern(
            first="sh",
            rest=(PatternToken(alternatives=("-c", "-l")),),
        ),
        decision=Decision.ALLOW,
    )
    assert policy.rules_by_program["bash"] == [expected_bash]
    assert policy.rules_by_program["sh"] == [expected_sh]
    assert policy.check(("bash", "-c", "echo", "hi"), allow_all) == Evaluation(
        decision=Decision.ALLOW,
        matched_rules=(prm(("bash", "-c"), Decision.ALLOW),),
    )
    assert policy.check(("sh", "-l", "echo", "hi"), allow_all) == Evaluation(
        decision=Decision.ALLOW,
        matched_rules=(prm(("sh", "-l"), Decision.ALLOW),),
    )


def test_tail_aliases_rule_snapshot():
    """对位 tail_aliases_are_not_cartesian_expanded 的规则快照部分（单规则多选）"""
    policy = parse_all([("test.rules", S_NPM_ALIASES)])
    assert policy.rules_by_program["npm"] == [
        PrefixRule(
            pattern=PrefixPattern(
                first="npm",
                rest=(
                    PatternToken(alternatives=("i", "install")),
                    PatternToken(alternatives=("--legacy-peer-deps", "--no-save")),
                ),
            ),
            decision=Decision.ALLOW,
        ),
    ]


def test_match_and_not_match_examples_accept_string_form():
    """对位 match_and_not_match_examples_are_enforced 的字符串样例形式
    （D2 闭环——parser.rs `shlex::split` 切词，对位 stdlib shlex posix 模式）"""
    policy = parse_all([("test.rules", S_EXAMPLES_RUST)])
    assert policy.check(("git", "status"), allow_all) == Evaluation(
        decision=Decision.ALLOW,
        matched_rules=(prm(("git", "status"), Decision.ALLOW),),
    )
    assert policy.check(
        ("git", "--config", "color.status=always", "status"), allow_all
    ) == Evaluation(
        decision=Decision.ALLOW,
        matched_rules=(
            hrm(
                Decision.ALLOW,
                ("git", "--config", "color.status=always", "status"),
            ),
        ),
    )


def test_justification_retained_on_rule():
    """对位 justification 两案的规则面（match 负载传递见
    test_justification_on_match_payload）"""
    policy = parse_all(
        [("test.rules", S_RM_FORBIDDEN), ("test.rules", S_LS_ALLOW_JUST)]
    )
    assert policy.rules_by_program["rm"][0].justification == "destructive command"
    assert policy.rules_by_program["ls"][0].justification == "safe and commonly used"


def test_network_rules_compile_into_domain_lists():
    """对位 network_rules_compile_into_domain_lists（解析 + 编译全案——D5 闭环）"""
    policy = parse_all([("network.rules", S_NETWORK)])
    rules = policy.network_rules
    assert len(rules) == 4
    assert rules[1].protocol is NetworkRuleProtocol.HTTPS
    assert [rule.host for rule in rules] == [
        "google.com",
        "api.github.com",
        "blocked.example.com",
        "prompt-only.example.com",
    ]
    assert [rule.decision for rule in rules] == [
        Decision.ALLOW,
        Decision.ALLOW,
        Decision.FORBIDDEN,  # "deny" → Forbidden（对位 parse_network_rule_decision）
        Decision.PROMPT,
    ]
    allowed, denied = policy.compiled_network_domains()
    assert allowed == ["google.com", "api.github.com"]
    assert denied == ["blocked.example.com"]


def test_append_allow_prefix_rule_text():
    """对位 append_allow_prefix_rule_dedupes_existing_rule 的写入文本
    （追加/去重语义见 test_append_rule_line_text_dedupes_and_newline_discipline；
    落盘归 harness RulesStore）"""
    line = format_prefix_rule(("python3",))
    assert line + "\n" == 'prefix_rule(pattern=["python3"], decision="allow")\n'


def test_empty_prefix_rejected_at_format_layer():
    """对位 add_prefix_rule_rejects_empty_prefix 的 format 层等价"""
    with pytest.raises(ValueError, match="at least one token"):
        format_prefix_rule(())


# =============================================================================
# host_executable 声明族（D3 闭环——对位 basic.rs parses/rejects/last_wins 五例）
# =============================================================================


def test_parses_host_executable_paths():
    """对位 parses_host_executable_paths（重复路径去重，保序）"""
    policy = parse_all([("test.rules", S_HOST_EXEC_DEDUP)])
    assert policy.host_executables()["git"] == [
        "/opt/homebrew/bin/git",
        "/usr/bin/git",
    ]


def test_host_executable_rejects_non_absolute_path():
    """对位 host_executable_rejects_non_absolute_path"""
    with pytest.raises(
        PolicyParseError, match="host_executable paths must be absolute"
    ):
        parse_policy("test.rules", 'host_executable(name = "git", paths = ["git"])')


def test_host_executable_rejects_name_with_path_separator():
    """对位 host_executable_rejects_name_with_path_separator"""
    with pytest.raises(
        PolicyParseError, match="host_executable name must be a bare executable name"
    ):
        parse_policy(
            "test.rules",
            'host_executable(name = "/usr/bin/git", paths = ["/usr/bin/git"])',
        )


def test_host_executable_rejects_path_with_wrong_basename():
    """对位 host_executable_rejects_path_with_wrong_basename"""
    with pytest.raises(PolicyParseError, match="must have basename `git`"):
        parse_policy(
            "test.rules", 'host_executable(name = "git", paths = ["/usr/bin/rg"])'
        )


def test_host_executable_last_definition_wins():
    """对位 host_executable_last_definition_wins（insert 覆盖语义）"""
    policy = parse_all(
        [
            ("shared.rules", 'host_executable(name = "git", paths = ["/usr/bin/git"])'),
            (
                "user.rules",
                'host_executable(name = "git", paths = ["/opt/homebrew/bin/git"])',
            ),
        ]
    )
    assert policy.host_executables()["git"] == ["/opt/homebrew/bin/git"]


# =============================================================================
# host 可执行解析求值族（D4 闭环——对位 basic.rs check_with_options 六例；
# 裸 check 默认不解析，故调用形状为 check_with_options(resolve_host_executables=True)）
# =============================================================================


def test_host_executable_resolution_uses_basename_rule_when_allowed():
    """对位 host_executable_resolution_uses_basename_rule_when_allowed"""
    policy = parse_all([("test.rules", S_HOST_EXEC_BASENAME_RULE)])
    evaluation = policy.check_with_options(
        ("/usr/bin/git", "status"), allow_all, RESOLVE_HOST_EXECUTABLES
    )
    assert evaluation == Evaluation(
        decision=Decision.PROMPT,
        matched_rules=(
            prm(("git", "status"), Decision.PROMPT, resolved_program="/usr/bin/git"),
        ),
    )
    assert evaluation.matched_rules[0].resolved_program == "/usr/bin/git"


def test_prefix_rule_examples_honor_host_executable_resolution():
    """对位 prefix_rule_examples_honor_host_executable_resolution：
    样例校验在 resolve_host_executables=True 下跑（解析即通过）"""
    parse_all([("test.rules", S_HOST_EXEC_EXAMPLES)])


def test_host_executable_resolution_respects_explicit_empty_allowlist():
    """对位 host_executable_resolution_respects_explicit_empty_allowlist：
    显式空 allowlist → basename 规则不命中 → 启发式兜底"""
    policy = parse_all([("test.rules", S_HOST_EXEC_EMPTY_ALLOWLIST)])
    evaluation = policy.check_with_options(
        ("/usr/bin/git", "status"), allow_all, RESOLVE_HOST_EXECUTABLES
    )
    assert evaluation == Evaluation(
        decision=Decision.ALLOW,
        matched_rules=(hrm(Decision.ALLOW, ("/usr/bin/git", "status")),),
    )


def test_host_executable_resolution_ignores_path_not_in_allowlist():
    """对位 host_executable_resolution_ignores_path_not_in_allowlist"""
    policy = parse_all([("test.rules", S_HOST_EXEC_ALLOWLIST)])
    evaluation = policy.check_with_options(
        ("/opt/homebrew/bin/git", "status"), allow_all, RESOLVE_HOST_EXECUTABLES
    )
    assert evaluation == Evaluation(
        decision=Decision.ALLOW,
        matched_rules=(hrm(Decision.ALLOW, ("/opt/homebrew/bin/git", "status")),),
    )


def test_host_executable_resolution_falls_back_without_mapping():
    """对位 host_executable_resolution_falls_back_without_mapping：
    无声明映射 → 放行 basename 匹配（resolved_program 随命中）"""
    policy = parse_all([("test.rules", S_GIT_PROMPT_ONLY)])
    evaluation = policy.check_with_options(
        ("/usr/bin/git", "status"), allow_all, RESOLVE_HOST_EXECUTABLES
    )
    assert evaluation == Evaluation(
        decision=Decision.PROMPT,
        matched_rules=(
            prm(("git",), Decision.PROMPT, resolved_program="/usr/bin/git"),
        ),
    )
    assert evaluation.matched_rules[0].resolved_program == "/usr/bin/git"


def test_host_executable_resolution_does_not_override_exact_match():
    """对位 host_executable_resolution_does_not_override_exact_match：
    精确命中优先（resolved_program 恒 None）"""
    policy = parse_all([("test.rules", S_HOST_EXEC_EXACT_MATCH)])
    evaluation = policy.check_with_options(
        ("/usr/bin/git", "status"), allow_all, RESOLVE_HOST_EXECUTABLES
    )
    assert evaluation == Evaluation(
        decision=Decision.ALLOW,
        matched_rules=(prm(("/usr/bin/git",), Decision.ALLOW),),
    )


# =============================================================================
# 命中负载（D6 闭环——对位 rule.rs RuleMatch 枚举形状）
# =============================================================================


def test_justification_on_match_payload():
    """对位 justification 两案的命中负载（rule.rs `matches` 的
    `justification: self.justification.clone()` 随命中克隆）"""
    policy = parse_all(
        [("test.rules", S_RM_FORBIDDEN), ("test.rules", S_LS_ALLOW_JUST)]
    )
    rm_eval = policy.check(("rm", "-rf", "/some/important/folder"), allow_all)
    assert rm_eval.matched_rules[0].justification == "destructive command"
    ls_eval = policy.check(("ls", "-l"), prompt_all)
    assert ls_eval.matched_rules[0].justification == "safe and commonly used"


def test_heuristics_match_carries_command_payload():
    """启发式命中携带命令词（Rust HeuristicsRuleMatch 对位；
    裁决装配件靠它产出"永远允许"写回候选）。"""
    evaluation = Policy.empty().check(("python",), prompt_all)
    assert evaluation.matched_rules[0].command == ("python",)


def test_match_payload_shape():
    """RuleMatch 字段形状（对位 rule.rs 枚举）——PrefixRuleMatch 四字段
    （resolved_program/justification 带默认值）、HeuristicsRuleMatch 两字段；
    后续新增字段只许带默认值追加"""
    assert [f.name for f in fields(PrefixRuleMatch)] == [
        "matched_prefix",
        "decision",
        "resolved_program",
        "justification",
    ]
    assert [f.name for f in fields(HeuristicsRuleMatch)] == ["decision", "command"]


# =============================================================================
# 写回文本（D7 闭环——文本语义归引擎纯函数；咨询式文件锁与落盘 I/O 归消费方
# harness RulesStore，文件行为实证见 nova_harness
# tests/core/harness/test_rules_store.py）
# =============================================================================


def test_append_rule_line_text_dedupes_and_newline_discipline():
    """对位 amend.rs `append_locked_line` 的纯文本语义（basic.rs
    append_allow_prefix_rule_dedupes_existing_rule 的文本面 + amend.rs 自带
    补换行两案的纯函数等价）"""
    line = 'prefix_rule(pattern=["python3"], decision="allow")'
    # 空 contents → 精确单行
    contents = append_rule_line_text("", line)
    assert contents == 'prefix_rule(pattern=["python3"], decision="allow")\n'
    # 二次追加去重（逐行精确相等 → 原样返回）
    assert append_rule_line_text(contents, line) == contents
    # 非空且不以 "\n" 结尾 → 先补 "\n" 再追加
    seeded = 'prefix_rule(pattern=["ls"], decision="allow")'
    assert append_rule_line_text(seeded, line) == seeded + "\n" + line + "\n"
    # 已以 "\n" 结尾 → 不多补（无空行）
    seeded_nl = seeded + "\n"
    assert append_rule_line_text(seeded_nl, line) == seeded_nl + line + "\n"


# =============================================================================
# 字面查找键（D8 终态）
# =============================================================================


def test_exact_path_rule_matches_literal_key():
    """D8 终态：check 以字面 cmd[0] 作查找键（Rust 默认 check 语义）。

    精确路径规则命中字面同路径命令；裸名/路径不互认（裸名归一归
    host_executable 声明族 + check_with_options——见 D3/D4 闭环区）。
    """
    policy = parse_policy(
        "test.rules", 'prefix_rule(pattern=["/usr/bin/git"], decision="allow")'
    )
    evaluation = policy.check(("/usr/bin/git", "status"), prompt_all)
    assert evaluation.decision is Decision.ALLOW
    assert evaluation.is_match() is True
    # 裸名命令不命中路径规则（字面语义）→ 启发式兜底
    bare = policy.check(("git", "status"), prompt_all)
    assert bare.decision is Decision.PROMPT
    assert bare.is_match() is False
