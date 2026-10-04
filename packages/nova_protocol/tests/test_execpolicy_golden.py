"""execpolicy 金标对跑——codex execpolicy tests/basic.rs（27 例）全量移植。

金标参照：原 `packages/nova-agent-rs/execpolicy` Rust 存档（**已删除**——对跑时
cargo test 27/27 绿（临时 workspace 搭在 /tmp/execpolicy-ws：存档源码未动一字，
仅 /tmp 副本的 Cargo.toml 对齐了命名漂移——lib 名 nova_executor_execpolicy、依赖
包名 nova-executor-utils-absolute-path → package nova-exec-server-utils-absolute-path）。
本文件即存档的活语义参照：对跑后 py 引擎与 Rust 在设计面内决策全等，已知窄化
以 strict xfail 固化。

矩阵映射（27 个 Rust 测试 → 本文件）：

- 行为完全一致 → EVAL_CASES / PARSE_ERROR_CASES 参数化 + 规则快照/部件测试：
  basic_match、justification_is_attached_to_forbidden_matches（决策+规则面）、
  justification_can_be_used_with_allow_decision、justification_cannot_be_empty、
  add_prefix_rule_extends_policy（API 适配：py add_prefix_rule 收 PrefixRule 对象）、
  add_prefix_rule_rejects_empty_prefix（分层等价：parse 层 + format 层）、
  parses_multiple_policy_files、tail_aliases_are_not_cartesian_expanded、
  match_and_not_match_examples_are_enforced（list 形式等价位；原样源见 D2）、
  strictest_decision_wins_across_matches、strictest_decision_across_multiple_commands、
  heuristics_match_is_returned_when_no_policy_matches、
  network_rules_compile_into_domain_lists（解析部分；编译部分见 D5）、
  network_rule_rejects_wildcard_hosts、
  append_allow_prefix_rule_dedupes_existing_rule（文本部分；落盘去重见 D7）。

- 已知分歧（py 窄化/缺口）→ test_divergence_*：xfail(strict=True) 钉住金标语义——
  断言写的是 Rust 金标行为，py 一旦补齐即 XPASS(strict) 强制回本节迁移；
  test_pin_* 钉住 py 当前行为，防静默漂移。分歧清单：
  D1  首 token 多选展开（only_first_token_alias_expands_to_multiple_rules）：
      Rust 按首 token 各备选展开为多规则；py 解析器拒绝（"首 token 定值"窄化）。
  D2  字符串形式 match/not_match 样例（Rust 经 shlex 切词接受；py 只收字符串列表）。
  D3  host_executable 声明族（parses_host_executable_paths /
      rejects_non_absolute_path / rejects_name_with_path_separator /
      rejects_path_with_wrong_basename / last_definition_wins）：py 无此规则调用。
  D4  check_with_options(resolve_host_executables) 求值族
      （uses_basename_rule_when_allowed / respects_explicit_empty_allowlist /
      ignores_path_not_in_allowlist / falls_back_without_mapping /
      does_not_override_exact_match / examples_honor_host_executable_resolution）：
      py 无 MatchOptions/allowlist/resolved_program，check 只做精确字面匹配。
  D5  compiled_network_domains 域名单编译（allow/deny upsert 语义 py 未移植）。
  D6  命中负载缺口：justification / heuristics command 不随 match 传递
      （resolved_program 缺口随 D4 一案）。
  D7  blocking_append_allow_prefix_rule 落盘去重（py 明文约定 I/O 归消费方，
      只有 format_prefix_rule 文本形态）。
  D8  【已闭环】精确路径规则查找键：check 已从 basename 归一改回字面 cmd[0]
      （Rust 默认语义）——test_exact_path_rule_matches_literal_key 金标固化。
"""

from dataclasses import fields

import nova_protocol.exec_server_policy as exec_server_policy_module
import pytest
from nova_protocol.exec_server_policy import (
    Decision,
    Evaluation,
    HeuristicsRuleMatch,
    NetworkRuleProtocol,
    PatternToken,
    Policy,
    PolicyParseError,
    PrefixPattern,
    PrefixRule,
    PrefixRuleMatch,
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
    """多规则文件按序合并（对位 PolicyParser 多次 parse + build 的聚合语义）"""
    merged = Policy.empty()
    for identifier, contents in sources:
        policy = parse_policy(identifier, contents)
        for rules in policy.rules_by_program.values():
            for rule in rules:
                merged.add_prefix_rule(rule)
        merged.network_rules.extend(policy.network_rules)
    return merged


def prm(prefix, decision) -> PrefixRuleMatch:
    return PrefixRuleMatch(matched_prefix=tuple(prefix), decision=decision)


def hrm(decision, command=None) -> HeuristicsRuleMatch:
    return HeuristicsRuleMatch(decision=decision, command=command)


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

#: basic.rs 原样源（含字符串形式样例）——D2 分歧用
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

#: 同一案例的 list 形式等价位（py 解析器子集可收）
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
    # justification_is_attached_to_forbidden_matches（决策+命中面前位；
    # justification 随 match 传递的部分见 D6）
    pytest.param(
        [("test.rules", S_RM_FORBIDDEN)],
        [("rm", "-rf", "/some/important/folder")],
        allow_all,
        Evaluation(
            decision=Decision.FORBIDDEN,
            matched_rules=(prm(("rm",), Decision.FORBIDDEN),),
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
            matched_rules=(prm(("ls",), Decision.ALLOW),),
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
    # match_and_not_match_examples_are_enforced（list 形式等价位；原样源见 D2）
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


def test_justification_retained_on_rule():
    """对位 justification 两案的规则面（match 负载传递见 D6）"""
    policy = parse_all(
        [("test.rules", S_RM_FORBIDDEN), ("test.rules", S_LS_ALLOW_JUST)]
    )
    assert policy.rules_by_program["rm"][0].justification == "destructive command"
    assert policy.rules_by_program["ls"][0].justification == "safe and commonly used"


def test_network_rules_parse_parts():
    """对位 network_rules_compile_into_domain_lists 的解析部分（编译部分见 D5）"""
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


def test_append_allow_prefix_rule_text():
    """对位 append_allow_prefix_rule_dedupes_existing_rule 的写入文本（去重见 D7）"""
    line = format_prefix_rule(("python3",))
    assert line + "\n" == 'prefix_rule(pattern=["python3"], decision="allow")\n'


def test_empty_prefix_rejected_at_format_layer():
    """对位 add_prefix_rule_rejects_empty_prefix 的 format 层等价"""
    with pytest.raises(ValueError, match="at least one token"):
        format_prefix_rule(())


# =============================================================================
# 已知分歧——xfail(strict) 钉住金标语义（py 补齐后 XPASS 强制迁移）
# =============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="D1: py 解析器拒绝首 token 多选；Rust 按备选展开为多规则",
)
def test_divergence_first_token_alias_expands_to_multiple_rules():
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


@pytest.mark.xfail(
    strict=True,
    reason="D2: py 只收字符串列表样例；Rust 另收字符串（shlex 切词）",
)
def test_divergence_string_form_examples():
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


@pytest.mark.xfail(
    strict=True,
    reason="D3: py 无 host_executable 规则调用（重复路径去重亦未移植）",
)
def test_divergence_parses_host_executable_paths():
    policy = parse_all([("test.rules", S_HOST_EXEC_DEDUP)])
    assert policy.host_executables()["git"] == [
        "/opt/homebrew/bin/git",
        "/usr/bin/git",
    ]


@pytest.mark.xfail(
    strict=True,
    reason="D3: py 无 host_executable 规则调用（非绝对路径拒绝亦未移植）",
)
def test_divergence_host_executable_rejects_non_absolute_path():
    with pytest.raises(
        PolicyParseError, match="host_executable paths must be absolute"
    ):
        parse_policy("test.rules", 'host_executable(name = "git", paths = ["git"])')


@pytest.mark.xfail(
    strict=True,
    reason="D3: py 无 host_executable 规则调用（裸名校验亦未移植）",
)
def test_divergence_host_executable_rejects_name_with_path_separator():
    with pytest.raises(
        PolicyParseError, match="host_executable name must be a bare executable name"
    ):
        parse_policy(
            "test.rules",
            'host_executable(name = "/usr/bin/git", paths = ["/usr/bin/git"])',
        )


@pytest.mark.xfail(
    strict=True,
    reason="D3: py 无 host_executable 规则调用（basename 校验亦未移植）",
)
def test_divergence_host_executable_rejects_path_with_wrong_basename():
    with pytest.raises(PolicyParseError, match="must have basename `git`"):
        parse_policy(
            "test.rules", 'host_executable(name = "git", paths = ["/usr/bin/rg"])'
        )


@pytest.mark.xfail(
    strict=True,
    reason="D3: py 无 host_executable 规则调用（后定义覆盖亦未移植）",
)
def test_divergence_host_executable_last_definition_wins():
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


@pytest.mark.xfail(
    strict=True,
    reason="D4: py 无 MatchOptions/allowlist/resolved_program（basename 解析求值）",
)
def test_divergence_host_executable_resolution_uses_basename_rule_when_allowed():
    policy = parse_all([("test.rules", S_HOST_EXEC_BASENAME_RULE)])
    # 金标：resolve_host_executables=true 时按 basename 命中，resolved_program 随命中
    evaluation = policy.check(("/usr/bin/git", "status"), allow_all)
    assert evaluation == Evaluation(
        decision=Decision.PROMPT,
        matched_rules=(prm(("git", "status"), Decision.PROMPT),),
    )
    assert evaluation.matched_rules[0].resolved_program == "/usr/bin/git"


@pytest.mark.xfail(
    strict=True,
    reason="D4: py 无 host_executable——样例校验亦不感知 host 映射",
)
def test_divergence_prefix_rule_examples_honor_host_executable_resolution():
    # 金标：解析即通过（validate_*_examples 在 resolve_host_executables=true 下跑）
    parse_all([("test.rules", S_HOST_EXEC_EXAMPLES)])


@pytest.mark.xfail(
    strict=True,
    reason="D4: py 无 MatchOptions/allowlist（显式空 allowlist 语义）",
)
def test_divergence_host_executable_resolution_respects_explicit_empty_allowlist():
    policy = parse_all([("test.rules", S_HOST_EXEC_EMPTY_ALLOWLIST)])
    # 金标：显式空 allowlist → basename 规则不命中 → 启发式兜底
    evaluation = policy.check(("/usr/bin/git", "status"), allow_all)
    assert evaluation == Evaluation(
        decision=Decision.ALLOW,
        matched_rules=(hrm(Decision.ALLOW, ("/usr/bin/git", "status")),),
    )


@pytest.mark.xfail(
    strict=True,
    reason="D4: py 无 MatchOptions/allowlist（非 allowlist 路径忽略语义）",
)
def test_divergence_host_executable_resolution_ignores_path_not_in_allowlist():
    policy = parse_all([("test.rules", S_HOST_EXEC_ALLOWLIST)])
    evaluation = policy.check(("/opt/homebrew/bin/git", "status"), allow_all)
    assert evaluation == Evaluation(
        decision=Decision.ALLOW,
        matched_rules=(hrm(Decision.ALLOW, ("/opt/homebrew/bin/git", "status")),),
    )


@pytest.mark.xfail(
    strict=True,
    reason="D4: py 无 MatchOptions（无映射时 basename 回退解析）",
)
def test_divergence_host_executable_resolution_falls_back_without_mapping():
    policy = parse_all([("test.rules", S_GIT_PROMPT_ONLY)])
    # 金标：resolve_host_executables=true 且无 git 映射 → 按 basename 命中 + resolved_program
    evaluation = policy.check(("/usr/bin/git", "status"), allow_all)
    assert evaluation == Evaluation(
        decision=Decision.PROMPT,
        matched_rules=(prm(("git",), Decision.PROMPT),),
    )
    assert evaluation.matched_rules[0].resolved_program == "/usr/bin/git"


@pytest.mark.xfail(
    strict=True,
    reason="D4: py 无 MatchOptions（精确命中优先于 basename 解析）",
)
def test_divergence_host_executable_resolution_does_not_override_exact_match():
    policy = parse_all([("test.rules", S_HOST_EXEC_EXACT_MATCH)])
    evaluation = policy.check(("/usr/bin/git", "status"), allow_all)
    assert evaluation == Evaluation(
        decision=Decision.ALLOW,
        matched_rules=(prm(("/usr/bin/git",), Decision.ALLOW),),
    )


@pytest.mark.xfail(
    strict=True,
    reason="D5: py 未移植 compiled_network_domains（allow/deny upsert 语义）",
)
def test_divergence_compiled_network_domains():
    policy = parse_all([("network.rules", S_NETWORK)])
    allowed, denied = policy.compiled_network_domains()
    assert allowed == ["google.com", "api.github.com"]
    assert denied == ["blocked.example.com"]


@pytest.mark.xfail(
    strict=True,
    reason="D6: py PrefixRuleMatch 不携带 justification（Rust 随命中传递）",
)
def test_divergence_justification_on_match_payload():
    policy = parse_all(
        [("test.rules", S_RM_FORBIDDEN), ("test.rules", S_LS_ALLOW_JUST)]
    )
    rm_eval = policy.check(("rm", "-rf", "/some/important/folder"), allow_all)
    assert rm_eval.matched_rules[0].justification == "destructive command"
    ls_eval = policy.check(("ls", "-l"), prompt_all)
    assert ls_eval.matched_rules[0].justification == "safe and commonly used"


def test_heuristics_match_carries_command_payload():
    """启发式命中携带命令词（D6 半边闭环——Rust HeuristicsRuleMatch 对位；
    裁决装配件靠它产出"永远允许"写回候选）。"""
    evaluation = Policy.empty().check(("python",), prompt_all)
    assert evaluation.matched_rules[0].command == ("python",)


@pytest.mark.xfail(
    strict=True,
    reason="D7: py 明文约定落盘 I/O 归消费方——无 blocking_append_allow_prefix_rule",
)
def test_divergence_append_allow_prefix_rule_dedupes(tmp_path):
    append = exec_server_policy_module.blocking_append_allow_prefix_rule
    policy_path = tmp_path / "rules" / "default.rules"
    append(policy_path, ("python3",))
    append(policy_path, ("python3",))
    assert (
        policy_path.read_text()
        == 'prefix_rule(pattern=["python3"], decision="allow")\n'
    )


# =============================================================================
# 分歧钉住——py 当前行为（与 xfail 成对；py 行为变化时双向报警）
# =============================================================================


def test_pin_first_token_alts_rejected():
    """D1 现状：py 解析器拒绝首 token 多选"""
    with pytest.raises(PolicyParseError, match="first pattern token must be a single"):
        parse_policy("test.rules", S_ALIAS_FIRST_TOKEN)


def test_pin_string_form_examples_rejected():
    """D2 现状：py 只收字符串列表样例"""
    with pytest.raises(PolicyParseError, match="example commands must be string lists"):
        parse_policy("test.rules", S_EXAMPLES_RUST)


def test_pin_host_executable_unknown_call():
    """D3/D4 现状：py 无 host_executable 规则调用"""
    with pytest.raises(PolicyParseError, match="unknown rule call `host_executable`"):
        parse_policy(
            "test.rules", 'host_executable(name = "git", paths = ["/usr/bin/git"])'
        )


def test_pin_match_payload_shape():
    """D6 现状：命中负载形状——PrefixRuleMatch 无 justification（xfail 钉住
    Rust 行为）；HeuristicsRuleMatch 已补 command（对位 Rust 同名字段）。"""
    assert [f.name for f in fields(PrefixRuleMatch)] == ["matched_prefix", "decision"]
    assert [f.name for f in fields(HeuristicsRuleMatch)] == ["decision", "command"]


def test_exact_path_rule_matches_literal_key():
    """D8 终态：check 以字面 cmd[0] 作查找键（Rust 默认 check 语义）。

    精确路径规则命中字面同路径命令；裸名/路径不互认（归一归
    host_executable 声明族——D3/D4 xfail 挂账）。
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
