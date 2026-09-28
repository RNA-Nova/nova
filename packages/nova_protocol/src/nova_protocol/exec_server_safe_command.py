"""exec 命令安全白名单（exec-server 前置管线·安全半边——对位 codex `is_safe_command.rs`）。

与 exec_server_intent 的危险初判互补：危险半边找"已知危险"，本模块证
"可验证安全"（verifiably safe）——按命令名分派 + per-command 参数校验器，
两侧都 fail-closed（判不出即不放行）。逐分支对位 Rust（非 Windows 部分）：

- `is_safe_command`：对位 `is_known_safe_command`（is_safe_command.rs:12-50）
  ——全词 zsh→bash 改写 → 单词命令分派 → `bash -lc` plain 序列逐段安全则
  复合安全。Windows 安全表与 PowerShell 相关件**不移植**（Windows/PS 专项
  批次另定；Rust 在非 Windows 下这些分支本就不编译/恒假，无缺省语义差）。
- `is_safe_to_call_with_exec`：对位同名私有分派器（:67-173）——简单白名单
  （cat/ls/…）、base64/find/rg 不安全选项表、git 只读子命令判定（全局选项
  防绕过 + 子命令参数只读 + branch 只读旗标）、`sed -n {N|M,N}p` 特批、
  Linux 限定 numfmt/tac（Rust 为 `cfg!(target_os = "linux")` 编译期门，
  本实现以 `platform.system()` 在导入期定值——sys/os 属枢纽禁入清单，
  platform 为纯洁性审计许可的纯环境查询）。
- git 选项表（:230-295）：Rust 的 GitOptionPattern 三态（Exact /
  ShortWithInlineValue / Prefix）按惯用形态折叠为"精确集 + 前缀组"——
  `Exact(x) ∪ ShortWithInlineValue(x)` 恒等于 `startswith(x)`，语义不变。

输入为已 tokenize 的命令词列表（`list[str]` / `tuple[str, ...]`）——与 Rust
同构：本模块吃解析产物，不自做分词；`bash -lc` 穿透复用
`exec_server_intent.parse_shell_lc_plain_commands`。

已知 py/Rust 分歧点（解析层，非本模块引入）：
- 词拼接（concatenation，如 `--out"put"`）在 py 手写解析下按词界切分为
  多词，Rust/tree-sitter 合并为一词——可能影响拼接成形的选项判定；
- `executable_name_lookup_key` 尾斜杠路径（"ls/"）py 判 None 更严；
  windows 侧 .exe/.cmd/.bat/.com 剥离与 lowercasing 挂账未做（本批只做
  basename 归一，Windows 批次另定）。

零行为零 I/O：纯表 + 纯判定。
"""

from __future__ import annotations

import platform

from .exec_server_intent import (
    executable_name_lookup_key,
    find_git_subcommand,
    parse_shell_lc_plain_commands,
)

#: Linux 限定命令门（对位 `cfg!(target_os = "linux")`——Rust 编译期定值，
#: 本实现进程导入期定值）
_IS_LINUX = platform.system() == "Linux"

# =============================================================================
# 入口（is_known_safe_command 对位）
# =============================================================================


def is_safe_command(command: list[str] | tuple[str, ...]) -> bool:
    """已 tokenize 的命令 → 是否为"可验证安全"（对位 `is_known_safe_command`）。

    两层判定（对位 is_safe_command.rs:12-50）：
    1. 整条命令按单词命令分派（`is_safe_to_call_with_exec`）；
    2. `bash -lc "..."`：脚本可解析为纯词命令序列（仅 &&/||/;/| 连接）且
       逐段都可验证安全 → 复合安全。空脚本不算（对位 `!all_commands.is_empty()`）。
    """
    # zsh 一律改写为 bash（对位 :13-22 的逐词映射——zsh/bash 的 -lc 解析同构）
    normalized = tuple("bash" if word == "zsh" else word for word in command)

    if is_safe_to_call_with_exec(normalized):
        return True

    plain_commands = parse_shell_lc_plain_commands(normalized)
    if plain_commands and all(is_safe_to_call_with_exec(cmd) for cmd in plain_commands):
        return True
    return False


# =============================================================================
# 单词命令分派（is_safe_to_call_with_exec 对位）
# =============================================================================

#: 简单白名单——整调即安全，无需参数校验（对位 :76-102 的 rustfmt::skip 表）
_SIMPLE_SAFE_COMMANDS = frozenset(
    {
        "cat",
        "cd",
        "cut",
        "echo",
        "expr",
        "false",
        "grep",
        "head",
        "id",
        "ls",
        "nl",
        "paste",
        "pwd",
        "rev",
        "seq",
        "stat",
        "tail",
        "tr",
        "true",
        "uname",
        "uniq",
        "wc",
        "which",
        "whoami",
    }
)

#: base64 写出文件的选项（对位 :105 UNSAFE_BASE64_OPTIONS）
_UNSAFE_BASE64_OPTIONS = frozenset({"-o", "--output"})

#: find 的不安全选项（对位 :119-126）：-exec 族可执行任意命令、-delete 删文件、
#: -fprint 族把路径写入文件
_UNSAFE_FIND_OPTIONS = frozenset(
    {
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-delete",
        "-fls",
        "-fprint",
        "-fprint0",
        "-fprintf",
    }
)

#: rg 带值的不安全选项（精确或 `--opt=value` 内联，对位 :135-140）：
#: --pre 对每个命中执行任意命令；--hostname-bin 以命令获取主机名
_UNSAFE_RIPGREP_OPTIONS_WITH_ARGS = ("--pre", "--hostname-bin")

#: rg 无值的不安全选项（仅精确命中，对位 :141-146）：
#: 会调起外部解压工具，出于谨慎不放行
_UNSAFE_RIPGREP_OPTIONS_WITHOUT_ARGS = frozenset({"--search-zip", "-z"})


def is_safe_to_call_with_exec(command: tuple[str, ...] | list[str]) -> bool:
    """单词命令分派（对位 `is_safe_to_call_with_exec` :67-173）。

    按可执行名（basename 归一）分派；不在白名单或参数校验不过 → False。
    """
    if not command:
        return False
    cmd = executable_name_lookup_key(command[0])
    if cmd is None:
        return False

    # Linux 限定（对位 :73 的 cfg!(target_os = "linux") 守卫——非 Linux 平台
    # 落入兜底的 False）
    if _IS_LINUX and cmd in ("numfmt", "tac"):
        return True

    if cmd in _SIMPLE_SAFE_COMMANDS:
        return True

    if cmd == "base64":
        # 对位 :104-112——任一 output 选项（分离/内联/短选项粘连）即不安全
        return not any(
            arg in _UNSAFE_BASE64_OPTIONS
            or arg.startswith("--output=")
            or (arg.startswith("-o") and arg != "-o")
            for arg in command[1:]
        )

    if cmd == "find":
        # 对位 :114-131——任何位置出现不安全选项即否（Rust 不跳过 argv[0]，同构）
        return not any(arg in _UNSAFE_FIND_OPTIONS for arg in command)

    if cmd == "rg":
        # 对位 :134-154——无值选项精确命中即否；带值选项精确或 `=` 内联即否
        return not any(
            arg in _UNSAFE_RIPGREP_OPTIONS_WITHOUT_ARGS
            or any(
                arg == opt or arg.startswith(opt + "=")
                for opt in _UNSAFE_RIPGREP_OPTIONS_WITH_ARGS
            )
            for arg in command
        )

    if cmd == "git":
        return _is_safe_git_command(command)

    if cmd == "sed":
        # 特批 `sed -n {N|M,N}p`（对位 :160-168 的匹配守卫：词数 ≤4、arg1 恰为
        # -n、arg2 匹配 /^(\d+,)?\d+p$/；守卫不过落入兜底 False）
        return (
            len(command) <= 4
            and len(command) > 1
            and command[1] == "-n"
            and _is_valid_sed_n_arg(command[2] if len(command) > 2 else None)
        )

    # ── anything else（对位 :170-171 的 `_ => false`） ──
    return False


# =============================================================================
# git 只读子命令判定（is_safe_git_command 对位）
# =============================================================================

#: 可验证安全的 git 子命令（对位 :177 find_git_subcommand 的目标集）
_SAFE_GIT_SUBCOMMANDS = ("status", "log", "diff", "show", "branch")

#: 不安全 git 全局选项·精确集（对位 :237-256 中 Exact 行）
_UNSAFE_GIT_GLOBAL_EXACT = frozenset(
    {
        "-C",
        "-c",
        "-p",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--paginate",
        "--super-prefix",
        "--work-tree",
    }
)

#: 不安全 git 全局选项·前缀组（对位 ShortWithInlineValue 与 Prefix 行：
#: `-C<dir>`/`-c<name=value>` 短选项内联值 + `--opt=value` 长选项内联值；
#: Exact(x) ∪ ShortWithInlineValue(x) 恒等于 startswith(x)，-C/-c 合入此处）
_UNSAFE_GIT_GLOBAL_PREFIX = (
    "-C",
    "-c",
    "--config-env=",
    "--exec-path=",
    "--git-dir=",
    "--namespace=",
    "--super-prefix=",
    "--work-tree=",
)

#: 不安全 git 子命令参数·精确集 + 前缀组（对位 :258-265）
_UNSAFE_GIT_SUBCOMMAND_EXACT = frozenset(
    {
        "--output",
        "--ext-diff",
        "--textconv",
        "--exec",
    }
)
_UNSAFE_GIT_SUBCOMMAND_PREFIX = ("--output=", "--exec=")

#: `git branch` 只读旗标（对位 :213-216）；`--format=` 前缀同列（:217-219）
_GIT_BRANCH_READ_ONLY_FLAGS = frozenset(
    {
        "--list",
        "-l",
        "--show-current",
        "-a",
        "--all",
        "-r",
        "--remotes",
        "-v",
        "-vv",
        "--verbose",
    }
)


def _is_safe_git_command(command: tuple[str, ...] | list[str]) -> bool:
    """git 分派（对位 `is_safe_git_command` :175-200）"""
    found = find_git_subcommand(command, _SAFE_GIT_SUBCOMMANDS)
    if found is None:
        return False
    subcommand_idx, subcommand = found

    # 全局选项段（argv[1..子命令]）含任一不安全选项即否——防 `-C`/`-c`/
    # `--git-dir` 类注入绕过
    global_args = command[1:subcommand_idx]
    if any(
        arg in _UNSAFE_GIT_GLOBAL_EXACT or arg.startswith(_UNSAFE_GIT_GLOBAL_PREFIX)
        for arg in global_args
    ):
        return False

    subcommand_args = command[subcommand_idx + 1 :]
    if subcommand in ("status", "log", "diff", "show"):
        return _git_subcommand_args_are_read_only(subcommand_args)
    if subcommand == "branch":
        return _git_subcommand_args_are_read_only(
            subcommand_args
        ) and _git_branch_is_read_only(subcommand_args)
    # 对位 :195-198 的 debug_assert 分支——目标集外的子命令不可达
    return False


def _git_subcommand_args_are_read_only(args: tuple[str, ...] | list[str]) -> bool:
    """子命令参数无写文件/执行外部命令选项（对位 :290-295）"""
    return not any(
        arg in _UNSAFE_GIT_SUBCOMMAND_EXACT
        or arg.startswith(_UNSAFE_GIT_SUBCOMMAND_PREFIX)
        for arg in args
    )


def _git_branch_is_read_only(branch_args: tuple[str, ...] | list[str]) -> bool:
    """`git branch` 参数明确为只读查询（对位 `git_branch_is_read_only` :204-228）。

    空参数即列分支；全为只读旗标/`--format=` 且至少一个 → 只读；其余任何
    参数都可能创建/改名/删除分支 → 否。
    """
    if not branch_args:
        return True
    saw_read_only_flag = False
    for arg in branch_args:
        if arg in _GIT_BRANCH_READ_ONLY_FLAGS or arg.startswith("--format="):
            saw_read_only_flag = True
        else:
            return False
    return saw_read_only_flag


# =============================================================================
# sed -n 参数校验（is_valid_sed_n_arg 对位）
# =============================================================================


def _is_valid_sed_n_arg(arg: str | None) -> bool:
    """是否匹配 /^(\\d+,)?\\d+p$/（对位 `is_valid_sed_n_arg` :304-334）。

    数字按 ASCII 判——Rust `is_ascii_digit`；py `str.isdigit()` 会放进
    全角/阿拉伯-印度数字，不得混用。
    """
    if arg is None or not arg.endswith("p"):
        return False
    parts = arg[:-1].split(",")
    if len(parts) == 1:
        # 单数字，如 "10p"
        return _is_ascii_digits(parts[0])
    if len(parts) == 2:
        # 区间，如 "1,5p"
        return _is_ascii_digits(parts[0]) and _is_ascii_digits(parts[1])
    # 多于一个逗号——非法
    return False


def _is_ascii_digits(text: str) -> bool:
    """非空且全为 ASCII 数字"""
    return bool(text) and all("0" <= ch <= "9" for ch in text)
