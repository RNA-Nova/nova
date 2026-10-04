"""exec_server_intent 测试——金标对位 codex is_dangerous_command.rs 测试用例"""

from nova_protocol.exec_server_intent import (
    DangerousCommandMatch,
    dangerous_command_match,
    intent_from_shell,
    parse_shell_lc_literal_commands,
    parse_shell_lc_plain_commands,
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


def test_bare_background_ampersand_is_opaque():
    """裸 &（后台运算符）→ opaque（回归：裸词扫描遇 & 即停而分隔符段不
    消费，曾无前进分支——'sleep 1 &' 死循环挂死裁决链，SIGALRM 实测复现；
    对位 bash.rs：& 不在白名单 punct 内 → 解析失败）。回归时本用例靠
    pytest-timeout 兜底变红。"""
    assert parse_shell_script_into_commands("sleep 1 &") is None
    assert parse_shell_script_into_commands("ls &") is None
    assert parse_shell_script_into_commands("echo a && ls &") is None
    assert parse_shell_script_into_commands("&") is None
    assert intent_from_shell("sleep 1 &").opaque is True
    # `&&` 不受影响（先匹配双字符运算符）
    assert parse_shell_script_into_commands("ls && echo ok") == [
        ("ls",),
        ("echo", "ok"),
    ]


# ── 动态词拒解析（codex bash.rs commit 4216123b3d 移植，上游测试逐条对位） ──


def test_plain_parse_rejects_runtime_expansion_in_plain_words():
    """对位 bash.rs rejects_runtime_expansion_in_plain_words（:424）——裸词含
    glob/brace/转义/tilde/equals/`#` 任一动态字符 → 整体 None。"""
    for script in [
        "find . -{delete,print}",
        "rg --pre{=,=sh} pattern payload.sh",
        "find . -del*",
        "find . -delet?",
        "find . -delet[e]",
        r"find . -de\lete",
        "echo ~",
        "echo ~HOME",
        "echo HEAD~1",
        "echo HEAD^",
        "echo file~",
        "echo =sh",
        "echo foo^bar",
        "echo foo#bar",
        "l* -l",
    ]:
        assert parse_shell_script_into_commands(script) is None, script
        # 上游另过 parse_shell_lc_plain_commands（bash/zsh × -c/-lc 四组合）；
        # 包装层仅做 shell/flag 校验，抽 bash -lc 与 zsh -c 两例对位
        assert parse_shell_lc_plain_commands(["bash", "-lc", script]) is None, script
        assert parse_shell_lc_plain_commands(["zsh", "-c", script]) is None, script


def test_plain_parse_preserves_quoted_literals():
    r"""对位 bash.rs preserves_quoted_literals（:452）——引号抑制展开，`~ ^ # = *`
    等在引号内保持字面。词拼接（`-g"*.py"`/`-"{a,b}"`）按 nova 已知分歧切分为
    多词（上游 tree-sitter 合并为 concatenation 单词，见模块说明）。"""
    assert parse_shell_script_into_commands(r'rg -g"*.py" pattern') == [
        ("rg", "-g", "*.py", "pattern")
    ]  # 上游合并 "-g*.py"
    assert parse_shell_script_into_commands(r'echo "\n"') == [("echo", "\\n")]
    assert parse_shell_script_into_commands(
        r"""echo "~HOME" 'HEAD~1' "HEAD^" 'foo#bar' "=sh" 'file~'"""
    ) == [("echo", "~HOME", "HEAD~1", "HEAD^", "foo#bar", "=sh", "file~")]
    assert parse_shell_script_into_commands("""echo -"{a,b}" '*?[]~^#=\\\\'""") == [
        ("echo", "-", "{a,b}", "*?[]~^#=\\\\")
    ]  # 上游合并 "-{a,b}"


def test_plain_parse_rejects_double_quoted_escapes():
    r"""对位 bash.rs rejects_double_quoted_escapes（:477）——双引号内
    `\$` ``\` `` `\"` `\\` `\<换行>` 源拼写≠运行时 argv → None。"""
    for script in [
        r'echo "\$HOME\`\"\\\n"',
        'find . "-de\\\nlete"',
        r'echo "\\"',
    ]:
        assert parse_shell_script_into_commands(script) is None, script
        assert parse_shell_lc_plain_commands(["bash", "-lc", script]) is None, script
        # literal 路径同样整体 None（nova 收紧点：上游 literal 半边仅省略该词，
        # 见 test_literal_path_dynamic_word_also_rejected）
        assert parse_shell_lc_literal_commands(["bash", "-lc", script]) is None, script


def test_plain_parse_accepts_double_quoted_newline_without_backslash():
    """对位 bash.rs accepts_double_quoted_strings_with_newlines——双引号内换行
    本身合法，仅反斜杠续行（`\\<换行>`）拒解析。"""
    assert parse_shell_script_into_commands('git commit -m "line1\nline2"') == [
        ("git", "commit", "-m", "line1\nline2")
    ]


def test_dynamic_word_probes_rejected():
    r"""实测探针（移植前逐条复现：`ls *.py` 放行成词、`ls ~/x` 放行、
    `ls a\ b` 错切两词、`echo "a\$b"` 保留反斜杠）——修复后两路径整体 None，
    Intent 置 opaque 上移审批层。"""
    for script in ["ls *.py", "ls ~/x", r"ls a\ b", 'echo "a\\$b"']:
        assert parse_shell_script_into_commands(script) is None, script
        assert parse_shell_lc_literal_commands(["bash", "-lc", script]) is None, script
        assert intent_from_shell(script).opaque is True, script


def test_literal_path_dynamic_word_also_rejected():
    """nova 收紧点（与上游不一致，理由在此）：上游 literal 半边对动态词仅"省略
    该词/命令名非字面丢整条"（bash.rs:243 parse_literal_shell_word → None 由调
    用方跳过），sibling 的 rm 类危险命令仍能提取；nova 手写解析器无节点粒度，
    统一整体 None——fail-open 边界不变（不可解析≠危险）：该脚本同时过不掉
    plain 解析（safe 白名单底座），审批层经 opaque 兜底，不会静默放行。"""
    assert dangerous_command_match(["bash", "-lc", "rm -rf /x; ls *.py"]) is None
    assert parse_shell_script_into_commands("rm -rf /x; ls *.py") is None


def test_lone_closing_brace_rejected_no_hang():
    """孤 `}`：裸词终止符集含 `}` 但无专属分支，词为空不前进曾死循环（同裸 `&`
    教训）；对位上游 `}` 在拒绝字符集内 → None。回归靠 pytest-timeout 兜底变红。"""
    assert parse_shell_script_into_commands("echo }") is None
    assert parse_shell_script_into_commands("}") is None


def test_plain_parse_rejects_variable_assignment_prefix():
    """对位 bash.rs rejects_variable_assignment_prefix——既有对位分支回归锁：
    赋值前缀 plain 拒绝不受动态词移植影响。"""
    assert parse_shell_script_into_commands("FOO=bar ls") is None
