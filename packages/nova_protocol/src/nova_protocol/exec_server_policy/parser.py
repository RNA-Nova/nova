"""规则文件解析器（对位 codex `execpolicy/src/parser.rs`）。

**声明式子集**——规则文件即 Python 语法兼容的
`prefix_rule(...)`/`network_rule(...)`/`host_executable(...)` 顶层调用序列
（codex 用 starlark 全量求值；本实现以 `ast` 静态提取字面参数，不执行任意
表达式——纯度与安全双纪律）。`match`/`not_match` 样例在解析末尾随规则校验
（对位 `pending_example_validations` + `validate_pending_examples_from`）。

解析器接受的字面量：字符串、字符串列表、嵌套字符串列表（PatternToken 多选）、
布尔/数字标量。其余表达式（变量/运算/函数调用/循环）→ PolicyParseError。
"""

from __future__ import annotations

import ast
import shlex

from ..exec_server_verdict import Decision
from .executable_name import executable_lookup_key, executable_path_lookup_key
from .policy import MatchOptions, Policy
from .rule import (
    NetworkRule,
    NetworkRuleProtocol,
    PatternToken,
    PrefixPattern,
    PrefixRule,
    normalize_network_rule_host,
)


class PolicyParseError(ValueError):
    """规则文件解析失败（带规则来源与行号）"""

    def __init__(self, source: str, line: int, detail: str):
        super().__init__(f"{source}:{line}: {detail}")
        self.source = source
        self.line = line
        self.detail = detail


_KNOWN_CALLS = {"prefix_rule", "network_rule", "host_executable"}


def parse_policy(source: str, contents: str) -> Policy:
    """规则文件 → Policy。

    `source` 用于错误定位（规则来源标识，对位 policy_identifier）。
    只接受顶层 `prefix_rule(...)`/`network_rule(...)`/`host_executable(...)`
    调用与字面参数；其余一切语句/表达式抛 PolicyParseError。
    """
    policy = Policy()
    pending_validations = []
    try:
        module = ast.parse(contents, filename=source)
    except SyntaxError as e:
        raise PolicyParseError(source, e.lineno or 0, f"syntax error: {e.msg}") from e
    for node in module.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            raise PolicyParseError(
                source, getattr(node, "lineno", 0), "expected a rule call statement"
            )
        call = node.value
        if not isinstance(call.func, ast.Name) or call.func.id not in _KNOWN_CALLS:
            name = getattr(call.func, "id", getattr(call.func, "attr", "?"))
            raise PolicyParseError(source, call.lineno, f"unknown rule call `{name}`")
        kwargs = {
            kw.arg: _literal(kw.value, source, call.lineno) for kw in call.keywords
        }
        if call.args:
            raise PolicyParseError(
                source, call.lineno, "rules take keyword arguments only"
            )
        if call.func.id == "prefix_rule":
            rules, matches, not_matches = _build_prefix_rules(
                kwargs, source, call.lineno
            )
            for rule in rules:
                policy.add_prefix_rule(rule)
            pending_validations.append((rules, matches, not_matches, call.lineno))
        elif call.func.id == "network_rule":
            policy._network_rules.append(
                _build_network_rule(kwargs, source, call.lineno)
            )
        else:
            name, paths = _build_host_executable(kwargs, source, call.lineno)
            policy.set_host_executable_paths(name, paths)
    _validate_pending_examples(policy, pending_validations, source)
    return policy


def _literal(node: ast.AST, source: str, line: int):
    """字面量提取：字符串/数字/布尔/None/列表（嵌套）——其余抛错"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_literal(elt, source, line) for elt in node.elts]
    raise PolicyParseError(source, line, "rule arguments must be literals")


def _build_prefix_rules(kwargs: dict, source: str, line: int):
    """prefix_rule 调用构建（对位 parser.rs `prefix_rule` 内建函数）。

    返回 (展开后的规则表, match 样例, not_match 样例)。**首 token 多选按
    备选展开为多条规则**（对位 parser.rs:384-396 `first_token.alternatives()`
    ——first=备选、rest=剩余 tokens；仅首 token 展开，尾部不笛卡尔展开）。
    样例不进规则本体——随 pending 校验（对位 `add_pending_example_validation`）。
    """
    unknown = set(kwargs) - {
        "pattern",
        "decision",
        "match",
        "not_match",
        "justification",
    }
    if unknown:
        raise PolicyParseError(
            source, line, f"unknown prefix_rule fields: {sorted(unknown)}"
        )
    raw_pattern = kwargs.get("pattern")
    if not isinstance(raw_pattern, list) or not raw_pattern:
        raise PolicyParseError(source, line, "pattern must be a non-empty list")
    tokens = [_pattern_token(item, source, line) for item in raw_pattern]
    first_token, rest = tokens[0], tuple(tokens[1:])
    raw_decision = kwargs.get("decision", "allow")
    if not isinstance(raw_decision, str):
        raise PolicyParseError(source, line, "decision must be a string")
    try:
        decision = Decision.parse(raw_decision)
    except ValueError as e:
        raise PolicyParseError(source, line, str(e)) from e
    justification = kwargs.get("justification")
    if justification is not None and not isinstance(justification, str):
        raise PolicyParseError(source, line, "justification must be a string")
    match_examples = _examples(kwargs.get("match"), source, line)
    not_match_examples = _examples(kwargs.get("not_match"), source, line)
    try:
        rules = [
            PrefixRule(
                pattern=PrefixPattern(first=head, rest=rest),
                decision=decision,
                justification=justification,
            )
            for head in first_token.alternatives
        ]
    except ValueError as e:
        raise PolicyParseError(source, line, str(e)) from e
    return rules, match_examples, not_match_examples


def _pattern_token(item, source: str, line: int) -> PatternToken:
    if isinstance(item, str):
        return PatternToken.single(item)
    if isinstance(item, list):
        if not item or not all(isinstance(alt, str) for alt in item):
            raise PolicyParseError(
                source, line, "pattern alternatives must be a non-empty string list"
            )
        if len(item) == 1:
            return PatternToken.single(item[0])
        return PatternToken(alternatives=tuple(item))
    raise PolicyParseError(
        source, line, "pattern tokens must be strings or string lists"
    )


def _examples(raw, source: str, line: int) -> tuple[tuple[str, ...], ...]:
    """样例列表提取（对位 parser.rs `parse_examples`）：每项为字符串
    （shlex 切词——对位 `parse_string_example`）或字符串列表（对位
    `parse_list_example`）。"""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PolicyParseError(
            source, line, "match/not_match must be a list of commands"
        )
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append(_string_example(item, source, line))
        elif isinstance(item, list):
            if not item:
                raise PolicyParseError(source, line, "example cannot be an empty list")
            if not all(isinstance(token, str) for token in item):
                raise PolicyParseError(source, line, "example tokens must be strings")
            out.append(tuple(item))
        else:
            raise PolicyParseError(
                source, line, "example must be a string or list of strings"
            )
    return tuple(out)


def _string_example(raw: str, source: str, line: int) -> tuple[str, ...]:
    """字符串样例 shlex 切词（对位 parser.rs:298 `shlex::split`——stdlib
    shlex 的 posix 模式与 Rust shlex 在 basic.rs 样例面上等价）"""
    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError:
        raise PolicyParseError(
            source, line, "example string has invalid shell syntax"
        ) from None
    if not tokens:
        raise PolicyParseError(source, line, "example cannot be an empty string")
    return tuple(tokens)


def _build_host_executable(kwargs: dict, source: str, line: int):
    """host_executable 声明构建（对位 parser.rs `host_executable` 内建函数）：

    裸名校验 + 逐条 path 校验（必须绝对路径、basename 必须等于 name、
    重复路径去重）。返回 (注册键, 路径表)——注册键经
    `executable_lookup_key`，后定义覆盖先定义（对位
    `host_executables_by_name` 的 insert 语义）。
    """
    unknown = set(kwargs) - {"name", "paths"}
    if unknown:
        raise PolicyParseError(
            source, line, f"unknown host_executable fields: {sorted(unknown)}"
        )
    name = kwargs.get("name")
    if not isinstance(name, str):
        raise PolicyParseError(source, line, "host_executable name must be a string")
    _validate_host_executable_name(name, source, line)
    raw_paths = kwargs.get("paths")
    if not isinstance(raw_paths, list):
        raise PolicyParseError(source, line, "host_executable paths must be a list")
    paths = []
    for raw in raw_paths:
        if not isinstance(raw, str):
            raise PolicyParseError(
                source, line, "host_executable paths must be strings"
            )
        # 对位 `parse_literal_absolute_path`（posix 绝对路径 = 以 "/" 开头）
        if not raw.startswith("/"):
            raise PolicyParseError(
                source, line, f"host_executable paths must be absolute (got {raw})"
            )
        if executable_path_lookup_key(raw) != executable_lookup_key(name):
            raise PolicyParseError(
                source,
                line,
                f"host_executable path `{raw}` must have basename `{name}`",
            )
        if raw not in paths:
            paths.append(raw)
    return executable_lookup_key(name), paths


def _validate_host_executable_name(name: str, source: str, line: int) -> None:
    """裸名校验（对位 parser.rs `validate_host_executable_name`）：非空、
    单路径组件且 `file_name == name`（禁含路径分隔符与 `.`/`..` 形态）"""
    if not name:
        raise PolicyParseError(source, line, "host_executable name cannot be empty")
    if "/" in name or executable_path_lookup_key(name) != name:
        raise PolicyParseError(
            source,
            line,
            f"host_executable name must be a bare executable name (got {name})",
        )


def _build_network_rule(kwargs: dict, source: str, line: int) -> NetworkRule:
    unknown = set(kwargs) - {"host", "protocol", "decision", "justification"}
    if unknown:
        raise PolicyParseError(
            source, line, f"unknown network_rule fields: {sorted(unknown)}"
        )
    host = kwargs.get("host")
    if not isinstance(host, str):
        raise PolicyParseError(source, line, "host must be a string")
    raw_protocol = kwargs.get("protocol")
    if not isinstance(raw_protocol, str):
        raise PolicyParseError(source, line, "protocol must be a string")
    raw_decision = kwargs.get("decision")
    if not isinstance(raw_decision, str):
        raise PolicyParseError(source, line, "decision must be a string")
    try:
        # 对位 parse_network_rule_decision："deny" → Forbidden，其余走标准解析
        decision = (
            Decision.FORBIDDEN
            if raw_decision == "deny"
            else Decision.parse(raw_decision)
        )
        return NetworkRule(
            host=normalize_network_rule_host(host),
            protocol=NetworkRuleProtocol.parse(raw_protocol),
            decision=decision,
            justification=kwargs.get("justification"),
        )
    except ValueError as e:
        raise PolicyParseError(source, line, str(e)) from e


def _validate_pending_examples(policy: Policy, pending: list, source: str) -> None:
    """样例校验（对位 parser.rs `validate_pending_examples_from`）：

    每次 prefix_rule 调用一份——校验域只含**该调用展开的规则**（不含他规），
    host 可执行映射取本文件解析末态（同文件的 `host_executable` 声明写在
    prefix_rule 之后亦可见——对位 codex 校验在整个文件求值完后跑）；
    `resolve_host_executables=True`（对位 rule.rs `validate_*_examples`
    的 MatchOptions）；not_match 先于 match（对位调用顺序）。
    """
    options = MatchOptions(resolve_host_executables=True)
    for rules, matches, not_matches, line in pending:
        rules_by_program: dict[str, list[PrefixRule]] = {}
        for rule in rules:
            rules_by_program.setdefault(rule.program, []).append(rule)
        validation_policy = Policy(
            rules_by_program=rules_by_program,
            host_executables_by_name=policy.host_executables(),
        )
        for example in not_matches:
            if validation_policy.matches_for_command_with_options(
                example, None, options
            ):
                raise PolicyParseError(
                    source,
                    line,
                    f"not_match example {example} unexpectedly matches rule pattern",
                )
        for example in matches:
            if not validation_policy.matches_for_command_with_options(
                example, None, options
            ):
                raise PolicyParseError(
                    source,
                    line,
                    f"match example {example} does not match rule pattern",
                )
