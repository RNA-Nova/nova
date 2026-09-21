"""exec 命令分析词汇与危险判定（exec-server 前置管线①站）。

解析与判定全部对位 codex `shell_command_tools`（nova-agent-rs 存档金标）：

- `parse_shell_script_into_commands` / `parse_shell_lc_literal_commands`：
  codex `bash.rs` 两个解析入口的**手写等价实现**。codex 用 tree-sitter；本
  实现按其接受/拒绝规则逐条对齐（引号剥壳、`$(...)`/反引号替换递归提取、
  赋值前缀跳过、控制结构配平校验、动态词省略、解析失败整体 None），无
  tree-sitter 依赖（枢纽纯度纪律）。obscure 语法（数组/进程替换/复杂
  heredoc）在本实现下退化为 None——与 codex 的 fail-open 边界一致（不可
  解析≠危险，由 Intent.opaque 上抛）。
- `dangerous_command_match`：`is_dangerous_command.rs` 完整移植（fail-closed
  包装深度上限 + rm -f 族 + sudo/env/trap/bash -lc 穿透）。
- `find_git_subcommand` 等 git 全局选项防绕过辅助（同文件金标）。

安全白名单（`is_safe_command.rs` 逐命令选项表，802 行）与 windows 危险/安全
表**本批未移植**——缺省更严而非更松（全过裁决），批次 C 随裁决子系统补。

`Intent` 是进程内值对象（不上线），frozen 锁死不可变。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# =============================================================================
# Intent——命令的结构化描述（进程内值对象）
# =============================================================================


@dataclass(frozen=True)
class Intent:
    """命令的结构化描述（①站产物——只描述，不裁决）。

    opaque=True：无法静态解析（解析失败/含动态展开），裁决压力上移询问层，
    不做悲观降级（对位 codex 的 fail-open 边界：不可解析≠危险）。
    """

    argv: tuple[str, ...]
    segments: tuple[tuple[str, ...], ...] = ()
    opaque: bool = False


def intent_from_argv(argv: tuple[str, ...] | list[str]) -> Intent:
    """已 tokenize 的 argv → Intent（单段）"""
    argv = tuple(argv)
    return Intent(argv=argv, segments=(argv,) if argv else (), opaque=False)


def intent_from_shell(command_text: str) -> Intent:
    """shell 命令文本 → Intent。

    plain 解析失败（含动态展开/不支持构造）→ opaque=True。
    """
    segments = parse_shell_script_into_commands(command_text)
    if segments is None:
        return Intent(argv=(), segments=(), opaque=True)
    return Intent(
        argv=segments[0] if segments else (),
        segments=tuple(segments),
        opaque=False,
    )


# =============================================================================
# shell 解析（codex bash.rs 的手写等价）
# =============================================================================

_SHELL_SEPARATORS = {"&&", "||", ";", "|"}
_CONTROL_PAIRS = {
    "if": "fi",
    "for": "done",
    "while": "done",
    "until": "done",
    "case": "esac",
}
_CONTROL_KEYWORDS = (
    set(_CONTROL_PAIRS)
    | set(_CONTROL_PAIRS.values())
    | {
        "then",
        "else",
        "elif",
        "do",
        "in",
        "function",
    }
)


def extract_bash_command(
    command: tuple[str, ...] | list[str],
) -> tuple[str, str] | None:
    """[shell, -lc|-c, script] 三元组提取（对位 codex extract_bash_command）"""
    if len(command) != 3:
        return None
    shell, flag, script = command[0], command[1], command[2]
    if flag not in ("-lc", "-c"):
        return None
    if executable_name_lookup_key(shell) not in ("bash", "zsh", "sh"):
        return None
    return (shell, script)


def parse_shell_lc_literal_commands(
    command: tuple[str, ...] | list[str],
) -> list[tuple[str, ...]] | None:
    """dangerous 路径：从 `bash -lc` 脚本提取全部字面命令（含嵌套替换/控制流）。

    对位 codex `parse_shell_lc_literal_commands`：解析失败（语法错误/未闭合）
    整体返回 None（fail-open 边界——不可解析不产命令）；动态词（变量展开等）
    从词表省略但不否决整条命令。
    """
    extracted = extract_bash_command(command)
    if extracted is None:
        return None
    _, script = extracted
    return _extract_literal_commands(script)


def parse_shell_script_into_commands(script: str) -> list[tuple[str, ...]] | None:
    """plain 路径：脚本仅由"纯词命令 + 安全运算符"组成时返回命令序列。

    对位 codex `try_parse_word_only_commands_sequence`：出现重定向、替换、
    展开、赋值、子壳、括号等任一非白名单构造 → 整体 None。
    """
    return _extract_literal_commands(script, plain_only=True)


def _extract_literal_commands(
    script: str, *, plain_only: bool = False
) -> list[tuple[str, ...]] | None:
    commands: list[tuple[str, ...]] = []
    words: list[str] = []
    saw_command_word = False
    control_stack: list[str] = []

    def flush_command() -> bool:
        nonlocal words, saw_command_word
        if saw_command_word:
            commands.append(tuple(words))
        words = []
        saw_command_word = False
        return True

    i = 0
    n = len(script)
    while i < n:
        ch = script[i]

        if ch.isspace():
            i += 1
            continue

        # ── 运算符/分隔符 ──
        if script.startswith("&&", i) or script.startswith("||", i):
            flush_command()
            i += 2
            continue
        if ch in ";|":
            flush_command()
            i += 1
            continue

        # ── 重定向：操作符与目标整体省略（plain 路径拒绝） ──
        if ch in "<>":
            if plain_only:
                return None
            j = i
            while j < n and script[j] in "<>&0123456789":
                j += 1
            i = j
            # 跳过重定向目标词（含引号目标）
            while i < n and script[i].isspace():
                i += 1
            i = _skip_word(script, i)
            continue

        # ── 单引号：整段字面，剥壳成一词 ──
        if ch == "'":
            end = script.find("'", i + 1)
            if end == -1:
                return None
            if saw_command_word or True:
                words.append(script[i + 1 : end])
                saw_command_word = True
            i = end + 1
            continue

        # ── 双引号：仅纯字面成词；含替换则省略本词并递归提取内层命令 ──
        if ch == '"':
            end = i + 1
            literal_chars: list[str] = []
            has_substitution = False
            closed = False
            while end < n:
                c = script[end]
                if c == '"':
                    closed = True
                    break
                if c == "\\":
                    if end + 1 < n:
                        literal_chars.append(script[end : end + 2])
                    end += 2
                    continue
                if script.startswith("$(", end):
                    if plain_only:
                        return None
                    close = _find_subst_end(script, end)
                    if close == -1:
                        return None
                    inner = _extract_literal_commands(script[end + 2 : close])
                    if inner is None:
                        return None
                    commands.extend(inner)
                    has_substitution = True
                    end = close + 1
                    continue
                if c == "`":
                    if plain_only:
                        return None
                    close = script.find("`", end + 1)
                    if close == -1:
                        return None
                    inner = _extract_literal_commands(script[end + 1 : close])
                    if inner is None:
                        return None
                    commands.extend(inner)
                    has_substitution = True
                    end = close + 1
                    continue
                if c == "$":
                    has_substitution = True
                    end += 1
                    continue
                literal_chars.append(c)
                end += 1
            if not closed:
                return None
            if not has_substitution:
                words.append("".join(literal_chars))
                saw_command_word = True
            i = end + 1
            continue

        # ── 命令替换：递归提取内层命令（本词省略；plain 拒绝） ──
        if script.startswith("$(", i):
            if plain_only:
                return None
            close = _find_subst_end(script, i)
            if close == -1:
                return None
            inner = _extract_literal_commands(script[i + 2 : close])
            if inner is None:
                return None
            commands.extend(inner)
            i = close + 1
            continue
        if ch == "`":
            if plain_only:
                return None
            close = script.find("`", i + 1)
            if close == -1:
                return None
            inner = _extract_literal_commands(script[i + 1 : close])
            if inner is None:
                return None
            commands.extend(inner)
            i = close + 1
            continue

        # ── 动态展开（$VAR/${...}）：词省略 ──
        if ch == "$":
            if plain_only:
                return None
            i = _skip_word(script, i + 1)
            continue

        # ── 子壳/括号/大括号：plain 拒绝；literal 词省略 ──
        if ch in "()":
            if plain_only:
                return None
            depth = 1
            i += 1
            while i < n and depth:
                if script[i] == "(":
                    depth += 1
                elif script[i] == ")":
                    depth -= 1
                i += 1
            if depth:
                return None
            continue
        if ch == "{":
            if plain_only:
                return None
            end = script.find("}", i + 1)
            if end == -1:
                return None
            i = end + 1
            continue

        # ── 裸词：literal 追加；控制关键字按结构配平 ──
        j = i
        while j < n and not script[j].isspace() and script[j] not in "'\";&|`(){}<>$":
            if script.startswith("&&", j) or script.startswith("||", j):
                break
            j += 1
        word = script[i:j]
        i = j

        if word in _CONTROL_KEYWORDS:
            if plain_only:
                return None
            if word in _CONTROL_PAIRS:
                control_stack.append(_CONTROL_PAIRS[word])
            elif word in _CONTROL_PAIRS.values():
                if not control_stack or control_stack.pop() != word:
                    return None
            flush_command()
            continue

        # 赋值前缀（命令词位置的 NAME=value）：跳过不计词
        if not saw_command_word and _is_assignment_word(word):
            continue

        words.append(word)
        saw_command_word = True

    flush_command()
    if control_stack:
        return None
    return commands


def _is_assignment_word(word: str) -> bool:
    if "=" not in word or word.startswith("="):
        return False
    name = word.split("=", 1)[0]
    return name.isidentifier()


def _skip_word(script: str, i: int) -> int:
    """跳过一个裸词/引号词（重定向目标/动态展开用），返回其后位置"""
    n = len(script)
    if i >= n:
        return i
    if script[i] == "'":
        end = script.find("'", i + 1)
        return n if end == -1 else end + 1
    if script[i] == '"':
        end = i + 1
        while end < n and script[end] != '"':
            end += 2 if script[end] == "\\" else 1
        return min(end + 1, n)
    while i < n and not script[i].isspace() and script[i] not in ";&|`":
        if script.startswith("&&", i) or script.startswith("||", i):
            break
        i += 1
    return i


def _find_subst_end(script: str, start: int) -> int:
    """`$(` 的配对 `)` 位置（处理嵌套与引号），找不到返回 -1"""
    depth = 1
    i = start + 2
    n = len(script)
    while i < n:
        ch = script[i]
        if ch == "'":
            end = script.find("'", i + 1)
            if end == -1:
                return -1
            i = end + 1
            continue
        if ch == '"':
            end = i + 1
            while end < n and script[end] != '"':
                end += 2 if script[end] == "\\" else 1
            if end >= n:
                return -1
            i = end + 1
            continue
        if script.startswith("$(", i):
            depth += 1
            i += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


# =============================================================================
# 危险命令初判（fail-closed——金标：shell_command_tools/is_dangerous_command.rs）
# =============================================================================


class DangerousCommandMatch(Enum):
    """命中的危险命令规则"""

    #: `rm` 带 force 选项
    FORCED_RM = "forced-rm"
    #: 其他危险命令规则命中（含包装深度溢出 fail-closed）
    OTHER = "other"


MAX_DANGEROUS_COMMAND_WRAPPER_DEPTH = 8


def dangerous_command_match(
    command: list[str] | tuple[str, ...],
) -> DangerousCommandMatch | None:
    """已 tokenize 的命令 → 命中的危险规则（未命中 None）"""
    return _dangerous_match_with_depth(tuple(command), 0)


def _dangerous_match_with_depth(
    command: tuple[str, ...], wrapper_depth: int
) -> DangerousCommandMatch | None:
    if wrapper_depth > MAX_DANGEROUS_COMMAND_WRAPPER_DEPTH:
        # fail-closed：包装层数超过识别深度上限，无法判定即按危险处理（Other），
        # 交由上层审批兜底，而不是静默放行。
        return DangerousCommandMatch.OTHER

    matched = _dangerous_match_for_exec(command, wrapper_depth)
    if matched is not None:
        return matched

    # bash -lc <script>：脚本内任一字面命令可能危险（含控制流与替换嵌套）
    literals = parse_shell_lc_literal_commands(command)
    if literals is not None:
        for segment in literals:
            matched = _dangerous_match_with_depth(segment, wrapper_depth + 1)
            if matched is not None:
                return matched

    return None


def executable_name_lookup_key(raw: str) -> str | None:
    """可执行名归一：取 basename（windows 侧另剥 .exe/.cmd/.bat/.com——
    精确级 windows 表挂账，本批只做 basename 归一）"""
    if not raw:
        return None
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    return name or None


def _is_git_global_option_with_value(arg: str) -> bool:
    return arg in (
        "-C",
        "-c",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--super-prefix",
        "--work-tree",
    )


def _is_git_global_option_with_inline_value(arg: str) -> bool:
    return (
        arg.startswith("--config-env=")
        or arg.startswith("--exec-path=")
        or arg.startswith("--git-dir=")
        or arg.startswith("--namespace=")
        or arg.startswith("--super-prefix=")
        or arg.startswith("--work-tree=")
        or ((arg.startswith("-C") or arg.startswith("-c")) and len(arg) > 2)
    )


def find_git_subcommand(
    command: list[str] | tuple[str, ...], subcommands: tuple[str, ...] | list[str]
) -> tuple[int, str] | None:
    """跳过 git 全局选项定位真子命令（防 `--git-dir` 类注入绕过）。

    共享语义（codex is_dangerous/is_safe 同一辅助）：第一个非选项 token 即
    子命令——不在目标集合内立即停扫，避免误伤后续位置参数（如分支名）。
    """
    if not command or executable_name_lookup_key(command[0]) != "git":
        return None

    skip_next = False
    for idx, arg in enumerate(command[1:], start=1):
        if skip_next:
            skip_next = False
            continue
        if _is_git_global_option_with_inline_value(arg):
            continue
        if _is_git_global_option_with_value(arg):
            skip_next = True
            continue
        if arg == "--" or arg.startswith("-"):
            continue
        if arg in subcommands:
            return (idx, arg)
        return None
    return None


def _dangerous_match_for_exec(
    command: tuple[str, ...], wrapper_depth: int
) -> DangerousCommandMatch | None:
    cmd0 = executable_name_lookup_key(command[0]) if command else None

    if cmd0 == "rm" and _rm_args_include_force_option(command[1:]):
        return DangerousCommandMatch.FORCED_RM

    # sudo <cmd>：直接检查 <cmd>
    if cmd0 == "sudo":
        return _dangerous_match_with_depth(command[1:], wrapper_depth + 1)

    # env：跳过环境赋值后检查实际命令
    if cmd0 == "env":
        return _dangerous_match_for_env(command, wrapper_depth)

    # trap：动作体是 shell 源码（首操作数），按 sh -c 检查
    if cmd0 == "trap":
        return _dangerous_match_for_trap(command, wrapper_depth)

    return None


def _dangerous_match_for_env(
    command: tuple[str, ...], wrapper_depth: int
) -> DangerousCommandMatch | None:
    command_index = 1
    while command_index < len(command):
        argument = command[command_index]
        if argument == "--":
            command_index += 1
            break
        name, _, _ = argument.partition("=")
        if argument in ("-i", "--ignore-environment") or (
            name and not name.startswith("-") and "=" in argument
        ):
            command_index += 1
            continue
        break
    return _dangerous_match_with_depth(command[command_index:], wrapper_depth + 1)


def _dangerous_match_for_trap(
    command: tuple[str, ...], wrapper_depth: int
) -> DangerousCommandMatch | None:
    action_index = 1
    if action_index < len(command) and command[action_index] == "--":
        action_index += 1
    if action_index >= len(command) or command[action_index].startswith("-"):
        return None
    action = command[action_index]
    return _dangerous_match_with_depth(("sh", "-c", action), wrapper_depth + 1)


def _rm_args_include_force_option(args: tuple[str, ...]) -> bool:
    for arg in args:
        if arg == "--":
            break
        if arg == "--force":
            return True
        if arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:]:
            return True
    return False


def is_dangerous_command(command: list[str] | tuple[str, ...]) -> bool:
    """危险初判（fail-closed 语义）：命中任一危险规则即真"""
    return dangerous_command_match(command) is not None
