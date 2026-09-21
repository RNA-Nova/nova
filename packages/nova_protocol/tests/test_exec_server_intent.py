"""exec_server_intent 测试——金标对位 codex is_dangerous_command.rs 测试用例"""

from nova_protocol.exec_server_intent import (
    DangerousCommandMatch,
    dangerous_command_match,
    find_git_subcommand,
    intent_from_shell,
    parse_shell_lc_literal_commands,
    parse_shell_script_into_commands,
)

# ── 危险判定（codex 测试用例逐条移植） ──


def test_rm_rf_is_dangerous():
    assert (
        dangerous_command_match(["rm", "-rf", "/"]) is DangerousCommandMatch.FORCED_RM
    )


def test_rm_f_is_dangerous():
    assert dangerous_command_match(["rm", "-f", "/"]) is DangerousCommandMatch.FORCED_RM


def test_forced_rm_variants_are_dangerous():
    for command in [
        ["/bin/rm", "-fr", "/tmp/example"],
        ["rm", "-r", "-f", "/tmp/example"],
        ["rm", "--force", "/tmp/example"],
        ["rm", "/tmp/example", "-f"],
        ["sudo", "rm", "-rf", "/tmp/example"],
        ["env", "TARGET=/tmp/example", "rm", "-rf", "/tmp/example"],
    ]:
        assert (
            dangerous_command_match(command) is DangerousCommandMatch.FORCED_RM
        ), command


def test_deeply_nested_command_wrappers_fail_closed():
    for depth, expected in [
        (8, DangerousCommandMatch.FORCED_RM),
        (9, DangerousCommandMatch.OTHER),
    ]:
        command = ["env"] * depth + ["rm", "-rf", "/tmp/example"]
        assert dangerous_command_match(command) is expected, (depth, expected)


def test_forced_rm_in_complex_shell_syntax_is_dangerous():
    for script in [
        "printf x | rm -rf /tmp/example",
        "if test -d /tmp/example; then rm --force /tmp/example; fi",
        'rm -rf "$TARGET" >/dev/null',
        'for target in /tmp/a /tmp/b; do rm -r -f "$target"; done',
        'echo "$(rm -rf /tmp/example)"',
        "bash -c 'rm -rf /tmp/example'",
        "trap 'rm -rf /tmp/example' EXIT",
    ]:
        assert (
            dangerous_command_match(["bash", "-lc", script])
            is DangerousCommandMatch.FORCED_RM
        ), script


def test_non_forced_or_non_literal_rm_is_not_dangerous():
    for command in [
        ["rm", "-r", "/tmp/example"],
        ["rm", "--", "-f"],
        ["bash", "-lc", "echo 'rm -rf /tmp/example'"],
        ["bash", "-lc", "cmd=rm; $cmd -rf /tmp/example"],
        [
            "bash",
            "-lc",
            "if then rm -rf /tmp/example",
        ],  # 语法错误 → None → 不判危险（codex fail-open 边界）
        ["env", "TARGET=/tmp/example", "rm", "-r", "/tmp/example"],
        ["bash", "-lc", "trap 'echo rm -rf /tmp/example' EXIT"],
    ]:
        assert dangerous_command_match(command) is None, command


# ── git 防绕过 ──


def test_find_git_subcommand_skips_global_options():
    assert find_git_subcommand(["git", "--git-dir=/x", "push"], ["push"]) == (2, "push")
    assert find_git_subcommand(["git", "-C", "/repo", "status"], ["status"]) == (
        3,
        "status",
    )
    assert find_git_subcommand(["git", "-c", "user.name=x", "log"], ["log"]) == (
        3,
        "log",
    )


def test_find_git_subcommand_stops_at_first_non_option_token():
    assert find_git_subcommand(["git", "checkout", "push"], ["push"]) is None
    assert find_git_subcommand(["ls", "push"], ["push"]) is None


# ── 解析器 ──


def test_literal_extraction_strips_quotes_and_substitutions():
    commands = parse_shell_lc_literal_commands(["bash", "-lc", 'echo "$(rm -rf /x)"'])
    assert commands is not None
    assert ("rm", "-rf", "/x") in commands


def test_literal_extraction_skips_assignment_prefix():
    commands = parse_shell_lc_literal_commands(["bash", "-lc", "FOO=bar rm -rf /x"])
    assert commands is not None
    assert ("rm", "-rf", "/x") in commands


def test_literal_extraction_syntax_error_returns_none():
    assert (
        parse_shell_lc_literal_commands(["bash", "-lc", "if then rm -rf /tmp/example"])
        is None
    )


def test_plain_parse_rejects_redirection():
    assert parse_shell_script_into_commands("echo x > /tmp/f") is None


def test_plain_parse_rejects_expansion():
    assert parse_shell_script_into_commands("echo $HOME") is None


def test_plain_parse_accepts_safe_sequence():
    assert parse_shell_script_into_commands("echo a && ls /tmp; cat f | grep x") == [
        ("echo", "a"),
        ("ls", "/tmp"),
        ("cat", "f"),
        ("grep", "x"),
    ]


def test_intent_opaque_on_unparseable():
    assert intent_from_shell("echo $HOME").opaque is True


def test_intent_from_shell_segments():
    intent = intent_from_shell("echo a && ls")
    assert intent.opaque is False
    assert intent.segments == (("echo", "a"), ("ls",))
