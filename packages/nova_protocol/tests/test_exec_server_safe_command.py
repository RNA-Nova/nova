"""exec_server_safe_command 测试——金标对位 is_safe_command.rs 测试用例（非 Windows 分支）。

Rust 侧 `is_safe_to_call_with_exec` / `is_known_safe_command` 的调用层分别
对位 py 的 `is_safe_to_call_with_exec` / `is_safe_command`；Windows/PowerShell
专项测试（windows_* / direct_powershell_* / non_windows_safe_classification_*）
不移植。文件尾部为 py 侧补充的解析对齐回归锚（bash.rs 拒绝规则对位）。
"""

import platform

import pytest

from nova_protocol.exec_server_safe_command import (
    is_safe_command,
    is_safe_to_call_with_exec,
)

_ON_LINUX = platform.system() == "Linux"


def _id(command: list[str]) -> str:
    return " ".join(command)


# ── known_safe_examples ──


@pytest.mark.parametrize(
    "command",
    [
        ["ls"],
        ["git", "status"],
        ["git", "branch"],
        ["git", "branch", "--show-current"],
        ["base64"],
        ["sed", "-n", "1,5p", "file.txt"],
        ["nl", "-nrz", "Cargo.toml"],
        # 无不安全选项的 find
        ["find", ".", "-name", "file.txt"],
    ],
    ids=_id,
)
def test_known_safe_examples(command):
    assert is_safe_to_call_with_exec(command)


@pytest.mark.parametrize(
    "command",
    [
        ["numfmt", "1000"],
        ["tac", "Cargo.toml"],
    ],
    ids=_id,
)
def test_linux_only_commands_follow_host_platform(command):
    """numfmt/tac 仅 Linux 安全（对位 Rust 的 cfg!(target_os = "linux") 分支断言）"""
    assert is_safe_to_call_with_exec(command) is _ON_LINUX


# ── git_branch_mutating_flags_are_not_safe / git_branch_global_options_respect_safety_rules ──


@pytest.mark.parametrize(
    "command",
    [
        ["git", "branch", "-d", "feature"],
        ["git", "branch", "new-branch"],
        ["bash", "-lc", "git branch -d feature"],
    ],
    ids=_id,
)
def test_git_branch_mutating_flags_are_not_safe(command):
    assert not is_safe_command(command)


def test_git_branch_read_only_flags_are_safe():
    assert is_safe_command(["git", "branch", "--show-current"])


# ── git_first_positional_is_the_subcommand ──


def test_git_first_positional_is_the_subcommand():
    # 首个非选项 token 即子命令；后续位置参数（如分支名）不得当作子命令
    assert not is_safe_command(["git", "checkout", "status"])


# ── git_output_flags_are_not_safe ──


@pytest.mark.parametrize(
    "command",
    [
        ["git", "log", "--output=/tmp/git-log-out-test", "-n", "1"],
        ["git", "diff", "--output", "/tmp/git-diff-out-test"],
        ["git", "show", "--output=/tmp/git-show-out-test", "HEAD"],
    ],
    ids=_id,
)
def test_git_output_flags_are_not_safe(command):
    assert not is_safe_command(command)


# ── git_global_pagination_flags_are_not_safe ──


@pytest.mark.parametrize(
    "command",
    [
        ["git", "--paginate", "log", "-1"],
        ["git", "-p", "log", "-1"],
        ["bash", "-lc", "git --paginate log -1"],
        ["bash", "-lc", "git -p log -1"],
    ],
    ids=_id,
)
def test_git_global_pagination_flags_are_not_safe(command):
    assert not is_safe_command(command)


# ── git_subcommand_patch_flags_remain_safe ──


@pytest.mark.parametrize(
    "command",
    [
        ["git", "log", "-p", "-1"],
        ["git", "diff", "-p"],
        ["git", "show", "-p", "HEAD"],
        ["bash", "-lc", "git log -p -1"],
    ],
    ids=_id,
)
def test_git_subcommand_patch_flags_remain_safe(command):
    assert is_safe_command(command)


# ── git_global_override_flags_are_not_safe ──


@pytest.mark.parametrize(
    "command",
    [
        ["git", "-C", ".", "status"],
        ["git", "-C.", "status"],
        ["git", "-c", "core.pager=cat", "log", "-n", "1"],
        ["git", "-ccore.pager=cat", "status"],
        ["git", "--config-env", "core.pager=PAGER", "show", "HEAD"],
        ["git", "--config-env=core.pager=PAGER", "show", "HEAD"],
        ["git", "--git-dir", ".evil-git", "diff", "HEAD~1..HEAD"],
        ["git", "--git-dir=.evil-git", "diff", "HEAD~1..HEAD"],
        ["git", "--work-tree", ".", "status"],
        ["git", "--work-tree=.", "status"],
        ["git", "--exec-path", ".git/helpers", "show", "HEAD"],
        ["git", "--exec-path=.git/helpers", "show", "HEAD"],
        ["git", "--namespace", "attacker", "show", "HEAD"],
        ["git", "--namespace=attacker", "show", "HEAD"],
        ["git", "--super-prefix", "attacker/", "show", "HEAD"],
        ["git", "--super-prefix=attacker/", "show", "HEAD"],
        ["bash", "-lc", "git -C .project-deps/test-fixtures status"],
        ["bash", "-lc", "git --git-dir=.evil-git diff HEAD~1..HEAD"],
    ],
    ids=_id,
)
def test_git_global_override_flags_are_not_safe(command):
    assert not is_safe_command(command)


# ── cargo_check_is_not_safe / zsh_lc_safe_command_sequence ──


def test_cargo_check_is_not_safe():
    assert not is_safe_command(["cargo", "check"])


def test_zsh_lc_safe_command_sequence():
    assert is_safe_command(["zsh", "-lc", "ls"])


# ── unknown_or_partial ──


@pytest.mark.parametrize(
    "command",
    [
        ["foo"],
        ["git", "fetch"],
        ["sed", "-n", "xp", "file.txt"],
        # 不安全 find 选项（执行任意命令/删文件/写文件）
        ["find", ".", "-name", "file.txt", "-exec", "rm", "{}", ";"],
        ["find", ".", "-name", "*.py", "-execdir", "python3", "{}", ";"],
        ["find", ".", "-name", "file.txt", "-ok", "rm", "{}", ";"],
        ["find", ".", "-name", "*.py", "-okdir", "python3", "{}", ";"],
        ["find", ".", "-delete", "-name", "file.txt"],
        ["find", ".", "-fls", "/etc/passwd"],
        ["find", ".", "-fprint", "/etc/passwd"],
        ["find", ".", "-fprint0", "/etc/passwd"],
        ["find", ".", "-fprintf", "/root/suid.txt", "%#m %u %p\n"],
    ],
    ids=_id,
)
def test_unknown_or_partial(command):
    assert not is_safe_to_call_with_exec(command)


# ── base64_output_options_are_unsafe ──


@pytest.mark.parametrize(
    "command",
    [
        ["base64", "-o", "out.bin"],
        ["base64", "--output", "out.bin"],
        ["base64", "--output=out.bin"],
        ["base64", "-ob64.txt"],
    ],
    ids=_id,
)
def test_base64_output_options_are_unsafe(command):
    assert not is_safe_to_call_with_exec(command)


# ── ripgrep_rules ──


def test_ripgrep_rules_safe():
    # 无不安全旗标的 rg 调用
    assert is_safe_to_call_with_exec(["rg", "Cargo.toml", "-n"])


@pytest.mark.parametrize(
    "command",
    [
        # 无值的不安全旗标（原样出现）
        ["rg", "--search-zip", "files"],
        ["rg", "-z", "files"],
        # 带值的不安全旗标（分离式与 = 内联式）
        ["rg", "--pre", "pwned", "files"],
        ["rg", "--pre=pwned", "files"],
        ["rg", "--hostname-bin", "pwned", "files"],
        ["rg", "--hostname-bin=pwned", "files"],
    ],
    ids=_id,
)
def test_ripgrep_rules_unsafe(command):
    assert not is_safe_to_call_with_exec(command)


# ── bash_lc_safe_examples / bash_lc_safe_examples_with_operators ──


@pytest.mark.parametrize(
    "command",
    [
        ["bash", "-lc", "ls"],
        ["bash", "-lc", "ls -1"],
        ["bash", "-lc", "git status"],
        ["bash", "-lc", 'grep -R "Cargo.toml" -n'],
        ["bash", "-lc", "sed -n 1,5p file.txt"],
        ["bash", "-lc", "sed -n '1,5p' file.txt"],
        ["bash", "-lc", "find . -name file.txt"],
    ],
    ids=_id,
)
def test_bash_lc_safe_examples(command):
    assert is_safe_command(command)


@pytest.mark.parametrize(
    "command",
    [
        ["bash", "-lc", 'grep -R "Cargo.toml" -n || true'],
        ["bash", "-lc", "ls && pwd"],
        ["bash", "-lc", "echo 'hi' ; ls"],
        ["bash", "-lc", "ls | wc -l"],
    ],
    ids=_id,
)
def test_bash_lc_safe_examples_with_operators(command):
    assert is_safe_command(command)


# ── bash_lc_unsafe_examples ──


@pytest.mark.parametrize(
    "command",
    [
        # 四词形态不可证安全
        ["bash", "-lc", "git", "status"],
        # 'git status' 整体引用 → 程序名含空格，不安全
        ["bash", "-lc", "'git status'"],
        # 不安全 find 选项不得放行
        ["bash", "-lc", "find . -name file.txt -delete"],
        # 序列中含不安全命令 → 拒绝
        ["bash", "-lc", "ls && rm -rf /"],
        # 括号/子壳在当前解析器下不可证安全
        ["bash", "-lc", "(ls)"],
        ["bash", "-lc", "ls || (pwd && echo hi)"],
        # 重定向拒绝
        ["bash", "-lc", "ls > out.txt"],
    ],
    ids=_id,
)
def test_bash_lc_unsafe_examples(command):
    assert not is_safe_command(command)


# =============================================================================
# py 侧补充回归锚（解析层对齐 bash.rs 拒绝规则 + ASCII 数字语义）
# =============================================================================


@pytest.mark.parametrize(
    "command",
    [
        # 阿拉伯-印度数字 U+0661 与上标²：py str.isdigit() 判真，
        # Rust is_ascii_digit 判假——金标按 ASCII
        ["sed", "-n", "١p", "file.txt"],
        ["sed", "-n", "²p", "file.txt"],
    ],
    ids=_id,
)
def test_sed_n_arg_requires_ascii_digits(command):
    assert not is_safe_to_call_with_exec(command)


@pytest.mark.parametrize(
    "command",
    [
        # 对位 bash.rs rejects_variable_assignment_prefix（plain 路径拒绝赋值
        # 前缀）——若放过，LD_PRELOAD 类环境注入会被安全侧放行
        ["bash", "-lc", "FOO=bar ls"],
        ["bash", "-lc", "LD_PRELOAD=/tmp/evil.so ls"],
    ],
    ids=_id,
)
def test_bash_lc_assignment_prefix_rejected(command):
    assert not is_safe_command(command)


@pytest.mark.parametrize(
    "command",
    [
        # 对位 bash.rs rejects_trailing_operator_parse_error
        ["bash", "-lc", "ls &&"],
        # 对位 rejects_empty_command_position_with_leading_operator
        ["bash", "-lc", "&& ls"],
        # 对位 rejects_empty_command_position_with_double_separator
        ["bash", "-lc", "ls ;; pwd"],
        # 对位 rejects_empty_command_position_with_empty_pipeline_segment
        ["bash", "-lc", "ls | | wc"],
    ],
    ids=_id,
)
def test_bash_lc_empty_command_positions_rejected(command):
    assert not is_safe_command(command)


def test_bash_lc_newline_separates_commands():
    # 换行即命令分隔（tree-sitter 语义对位）：两条安全命令 → 安全
    assert is_safe_command(["bash", "-lc", "ls\npwd"])
    # 若换行被当作空白粘连，("git", "status") 会误判安全；
    # 分隔后裸 `git` 无子命令 → 不安全
    assert not is_safe_command(["bash", "-lc", "git\nstatus"])


@pytest.mark.parametrize(
    "command",
    [
        [],  # 空词列表（Rust first() → None）
        ["bash", "-lc"],  # 缺脚本（extract_bash_command 要求三词）
        ["bash", "-lc", ""],  # 空脚本（Rust !all_commands.is_empty() 守卫）
    ],
    ids=_id,
)
def test_empty_command_and_empty_script(command):
    assert not is_safe_command(command)


def test_tuple_input_accepted():
    """同构词列表：tuple 形参与 list 同语义"""
    assert is_safe_command(("git", "status"))
    assert not is_safe_command(("git", "fetch"))
