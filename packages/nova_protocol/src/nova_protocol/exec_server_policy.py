"""exec 裁决引擎（execpolicy py 移植——金标：nova-agent-rs/execpolicy）+ 沙箱拒绝启发式。

组成（逐件对位，不臆造）：

- 规则语言（`decision.rs`/`rule.rs`）：`prefix_rule`/`network_rule` 两类规则；
  `PrefixPattern` 前缀匹配（首 token 定值 + 后续 PatternToken 单值/多选）。
- 解析器（`parser.rs`）：**声明式子集**——规则文件即 Python 语法兼容的
  `prefix_rule(...)`/`network_rule(...)` 顶层调用序列（codex 用 starlark
  全量求值；本实现以 `ast` 静态提取字面参数，不执行任意表达式——纯度与
  安全双纪律）。`match`/`not_match` 样例在解析时随规则校验（对位
  validate_match_examples）。
- 求值（`policy.rs`）：`Policy.check(cmd, heuristics_fallback)` →
  `Evaluation{decision: max(matched), matched_rules}`——多命中取最严格档；
  无规则命中交 heuristics_fallback（危险初判）。
- 写回文本（`amend.rs` 的文本形态）：`format_prefix_rule`/`format_network_rule`
  产规则行；落盘 I/O 归消费方（规则写门）。
- `is_likely_sandbox_denied`（sandboxing `denial.rs`）：沙箱拒绝启发式——
  "无确定性判别法"，按退出码快排 + 关键词 + seccomp SIGSYS 特例。

解析器接受的字面量：字符串、字符串列表、嵌套字符串列表（PatternToken 多选）、
布尔/数字标量。其余表达式（变量/运算/函数调用/循环）→ PolicyParseError。
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from enum import Enum

from .exec_server_intent import executable_name_lookup_key
from .exec_server_verdict import Decision


class PolicyParseError(ValueError):
    """规则文件解析失败（带规则来源与行号）"""

    def __init__(self, source: str, line: int, detail: str):
        super().__init__(f"{source}:{line}: {detail}")
        self.source = source
        self.line = line
        self.detail = detail


# =============================================================================
# 规则模式（rule.rs）
# =============================================================================


@dataclass(frozen=True)
class PatternToken:
    """单 token 匹配：定值或多选（多选单值时坍缩为定值——对位 parse_pattern_token）"""

    alternatives: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.alternatives:
            raise ValueError("pattern alternatives cannot be empty")

    @classmethod
    def single(cls, value: str) -> PatternToken:
        return cls(alternatives=(value,))

    def matches(self, token: str) -> bool:
        return token in self.alternatives


@dataclass(frozen=True)
class PrefixPattern:
    """前缀匹配（首 token 定值——按它索引规则）"""

    first: str
    rest: tuple[PatternToken, ...] = ()

    def matches_prefix(self, cmd: tuple[str, ...]) -> tuple[str, ...] | None:
        pattern_length = len(self.rest) + 1
        if len(cmd) < pattern_length or cmd[0] != self.first:
            return None
        for pattern_token, cmd_token in zip(self.rest, cmd[1:pattern_length]):
            if not pattern_token.matches(cmd_token):
                return None
        return cmd[:pattern_length]


# =============================================================================
# 规则本体
# =============================================================================


@dataclass(frozen=True)
class PrefixRule:
    """`prefix_rule(pattern, decision?, match?, not_match?, justification?)`"""

    pattern: PrefixPattern
    decision: Decision = Decision.ALLOW
    justification: str | None = None
    match_examples: tuple[tuple[str, ...], ...] = ()
    not_match_examples: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if self.justification is not None and not self.justification.strip():
            raise ValueError("justification cannot be empty")
        # 样例随规则校验（对位 validate_match_examples/validate_not_match_examples）
        for example in self.match_examples:
            if self.pattern.matches_prefix(example) is None:
                raise ValueError(f"match example {example} does not match rule pattern")
        for example in self.not_match_examples:
            if self.pattern.matches_prefix(example) is not None:
                raise ValueError(
                    f"not_match example {example} unexpectedly matches rule pattern"
                )

    @property
    def program(self) -> str:
        return self.pattern.first


class NetworkRuleProtocol(Enum):
    """网络规则协议（对位 NetworkRuleProtocol 含别名解析）"""

    HTTP = "http"
    HTTPS = "https"
    SOCKS5_TCP = "socks5_tcp"
    SOCKS5_UDP = "socks5_udp"

    @classmethod
    def parse(cls, raw: str) -> NetworkRuleProtocol:
        aliases = {
            "http": cls.HTTP,
            "https": cls.HTTPS,
            "https_connect": cls.HTTPS,
            "http-connect": cls.HTTPS,
            "socks5_tcp": cls.SOCKS5_TCP,
            "socks5_udp": cls.SOCKS5_UDP,
        }
        try:
            return aliases[raw]
        except KeyError:
            raise ValueError(
                "network_rule protocol must be one of http, https, socks5_tcp, socks5_udp"
            ) from None


def normalize_network_rule_host(raw: str) -> str:
    """网络规则主机归一（对位 normalize_network_rule_host 逐条）：

    去空白/尾点/小写；剥 host:port 与 [v6]:port；拒绝空、含 scheme/path、
    通配符、空白字符。
    """
    host = raw.strip()
    if not host:
        raise ValueError("network_rule host cannot be empty")
    if "://" in host or "/" in host or "?" in host or "#" in host:
        raise ValueError(
            "network_rule host must be a hostname or IP literal (without scheme or path)"
        )

    if host.startswith("["):
        closing = host.find("]")
        if closing == -1:
            raise ValueError("network_rule host has an invalid bracketed IPv6 literal")
        inside, rest = host[1:closing], host[closing + 1 :]
        if rest:
            if not rest.startswith(":") or not rest[1:] or not rest[1:].isdigit():
                raise ValueError(
                    f"network_rule host contains an unsupported suffix: {raw}"
                )
        host = inside
    elif host.count(":") == 1:
        candidate, _, port = host.rpartition(":")
        if candidate and port and port.isdigit():
            host = candidate

    normalized = host.rstrip(".").strip().lower()
    if not normalized:
        raise ValueError("network_rule host cannot be empty")
    if "*" in normalized:
        raise ValueError(
            "network_rule host must be a specific host; wildcards are not allowed"
        )
    if any(c.isspace() for c in normalized):
        raise ValueError("network_rule host cannot contain whitespace")
    return normalized


@dataclass(frozen=True)
class NetworkRule:
    """`network_rule(host, protocol, decision, justification?)`"""

    host: str
    protocol: NetworkRuleProtocol
    decision: Decision
    justification: str | None = None

    def __post_init__(self) -> None:
        if self.justification is not None and not self.justification.strip():
            raise ValueError("justification cannot be empty")


# =============================================================================
# 求值（policy.rs）
# =============================================================================


@dataclass(frozen=True)
class PrefixRuleMatch:
    """规则命中记录（含命中的前缀）"""

    matched_prefix: tuple[str, ...]
    decision: Decision


@dataclass(frozen=True)
class HeuristicsRuleMatch:
    """启发式兜底命中（非规则——is_match() 判假，对位 RuleMatch::Heuristics）"""

    decision: Decision


@dataclass(frozen=True)
class Evaluation:
    """求值结果：决策 = 全部命中的最严格档（max）"""

    decision: Decision
    matched_rules: tuple[PrefixRuleMatch | HeuristicsRuleMatch, ...]

    def is_match(self) -> bool:
        """是否规则命中（启发式兜底不算规则命中）"""
        return any(isinstance(m, PrefixRuleMatch) for m in self.matched_rules)


class Policy:
    """规则集：prefix 规则按 program 索引 + 网络规则 + host 可执行路径映射"""

    def __init__(
        self,
        rules_by_program: dict[str, list[PrefixRule]] | None = None,
        network_rules: list[NetworkRule] | None = None,
    ) -> None:
        self._rules_by_program = rules_by_program or {}
        self._network_rules = network_rules or []

    @classmethod
    def empty(cls) -> Policy:
        return cls()

    @property
    def rules_by_program(self) -> dict[str, list[PrefixRule]]:
        return self._rules_by_program

    @property
    def network_rules(self) -> list[NetworkRule]:
        return self._network_rules

    def add_prefix_rule(self, rule: PrefixRule) -> None:
        self._rules_by_program.setdefault(rule.program, []).append(rule)

    def get_allowed_prefixes(self) -> list[tuple[str, ...]]:
        """全部 allow 规则的前缀（对位 get_allowed_prefixes）"""
        out: list[tuple[str, ...]] = []
        for rules in self._rules_by_program.values():
            for rule in rules:
                if rule.decision is Decision.ALLOW:
                    out.append(
                        (rule.pattern.first,)
                        + tuple(t.alternatives[0] for t in rule.pattern.rest)
                    )
        return out

    def check(
        self,
        cmd: tuple[str, ...] | list[str],
        heuristics_fallback=None,
    ) -> Evaluation:
        """对一条命令求值：全部规则匹配 + 兜底 → Evaluation（decision 取 max）"""
        cmd = tuple(cmd)
        matched: list[PrefixRuleMatch | HeuristicsRuleMatch] = []
        program = executable_name_lookup_key(cmd[0]) if cmd else None
        if program is not None:
            for rule in self._rules_by_program.get(program, []):
                prefix = rule.pattern.matches_prefix(cmd)
                if prefix is not None:
                    matched.append(
                        PrefixRuleMatch(matched_prefix=prefix, decision=rule.decision)
                    )
        if not matched and heuristics_fallback is not None:
            decision = heuristics_fallback(cmd)
            if decision is not None:
                matched.append(HeuristicsRuleMatch(decision=decision))
        if not matched:
            return Evaluation(decision=Decision.ALLOW, matched_rules=())
        decision = max((m.decision for m in matched), key=lambda d: _decision_rank(d))
        return Evaluation(decision=decision, matched_rules=tuple(matched))

    def check_multiple(
        self, commands: list[tuple[str, ...]], heuristics_fallback=None
    ) -> Evaluation:
        """多命令聚合求值（复合命令逐段——对位 check_multiple）"""
        all_matched: list[PrefixRuleMatch | HeuristicsRuleMatch] = []
        for cmd in commands:
            evaluation = self.check(cmd, heuristics_fallback)
            all_matched.extend(evaluation.matched_rules)
        if not all_matched:
            return Evaluation(decision=Decision.ALLOW, matched_rules=())
        decision = max(
            (m.decision for m in all_matched), key=lambda d: _decision_rank(d)
        )
        return Evaluation(decision=decision, matched_rules=tuple(all_matched))


def _decision_rank(decision: Decision) -> int:
    return (Decision.ALLOW, Decision.PROMPT, Decision.FORBIDDEN).index(decision)


# =============================================================================
# 解析器（声明式子集——ast 静态提取，不执行表达式）
# =============================================================================

_KNOWN_CALLS = {"prefix_rule", "network_rule"}


def parse_policy(source: str, contents: str) -> Policy:
    """规则文件 → Policy。

    `source` 用于错误定位（规则来源标识，对位 policy_identifier）。
    只接受顶层 `prefix_rule(...)`/`network_rule(...)` 调用与字面参数；
    其余一切语句/表达式抛 PolicyParseError。
    """
    policy = Policy()
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
            policy.add_prefix_rule(_build_prefix_rule(kwargs, source, call.lineno))
        else:
            policy._network_rules.append(
                _build_network_rule(kwargs, source, call.lineno)
            )
    return policy


def _literal(node: ast.AST, source: str, line: int):
    """字面量提取：字符串/数字/布尔/None/列表（嵌套）——其余抛错"""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_literal(elt, source, line) for elt in node.elts]
    raise PolicyParseError(source, line, "rule arguments must be literals")


def _build_prefix_rule(kwargs: dict, source: str, line: int) -> PrefixRule:
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
    first_token = tokens[0]
    if len(first_token.alternatives) != 1:
        raise PolicyParseError(
            source, line, "first pattern token must be a single string"
        )
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
        return PrefixRule(
            pattern=PrefixPattern(
                first=first_token.alternatives[0], rest=tuple(tokens[1:])
            ),
            decision=decision,
            justification=justification,
            match_examples=match_examples,
            not_match_examples=not_match_examples,
        )
    except ValueError as e:
        raise PolicyParseError(source, line, str(e)) from e


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
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PolicyParseError(
            source, line, "match/not_match must be a list of commands"
        )
    out = []
    for item in raw:
        if not isinstance(item, list) or not all(isinstance(tok, str) for tok in item):
            raise PolicyParseError(
                source, line, "example commands must be string lists"
            )
        out.append(tuple(item))
    return tuple(out)


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


# =============================================================================
# 写回文本（amend.rs 的文本形态——I/O 归消费方）
# =============================================================================


def format_prefix_rule(command_prefix: tuple[str, ...] | list[str]) -> str:
    """`prefix_rule(pattern=["a", "b"], decision="allow")` 规则行（JSON 引号——
    对位 amend.rs 的 serde_json::to_string 语义）"""
    if not command_prefix:
        raise ValueError("prefix rule requires at least one token")
    tokens = ", ".join(
        json.dumps(token, ensure_ascii=False) for token in command_prefix
    )
    return f'prefix_rule(pattern=[{tokens}], decision="allow")'


def format_network_rule(
    host: str,
    protocol: NetworkRuleProtocol,
    decision: Decision,
    justification: str | None = None,
) -> str:
    """`network_rule(...)` 规则行"""
    host = normalize_network_rule_host(host)
    parts = [
        f"host={json.dumps(host, ensure_ascii=False)}",
        f"protocol={json.dumps(protocol.value)}",
        f"decision={json.dumps(decision.value)}",
    ]
    if justification is not None:
        parts.append(f"justification={json.dumps(justification, ensure_ascii=False)}")
    return f"network_rule({', '.join(parts)})"


# =============================================================================
# 沙箱拒绝启发式（sandboxing denial.rs 移植）
# =============================================================================

#: 执行机沙箱拒绝的输出关键词（小写匹配）
SANDBOX_DENIED_KEYWORDS: tuple[str, ...] = (
    "operation not permitted",
    "permission denied",
    "read-only file system",
    "seccomp",
    "sandbox",
    "landlock",
    "failed to write file",
)

#: shell 自身的快速拒绝码（语法错误/不可执行/命令不存在——不是沙箱干的）
_QUICK_REJECT_EXIT_CODES = (2, 126, 127)

#: seccomp 直接杀进程：128 + SIGSYS(31)
_SECCOMP_SIGSYS_EXIT_CODE = 159


def is_likely_executor_managed_sandbox_denied(
    exit_code: int, stdout: str, stderr: str, aggregated: str | None = None
) -> bool:
    """执行机托管沙箱拒绝判别（输出关键词启发式——对位同名函数）"""
    if exit_code == 0:
        return False
    sections = [stdout, stderr] + ([aggregated] if aggregated else [])
    return any(
        keyword in section.lower()
        for section in sections
        for keyword in SANDBOX_DENIED_KEYWORDS
    )


def is_likely_sandbox_denied(
    sandbox_type: str | None,
    exit_code: int,
    stdout: str,
    stderr: str,
    aggregated: str | None = None,
) -> bool:
    """沙箱拒绝启发式（对位 is_likely_sandbox_denied）。

    sandbox_type：None（未沙箱）/"linux_seccomp"/其他——None 或零退出码直接判否；
    快速拒绝码判否；seccomp 沙箱下 128+SIGSYS 判真；关键词判真。
    """
    if sandbox_type is None or exit_code == 0:
        return False
    if is_likely_executor_managed_sandbox_denied(exit_code, stdout, stderr, aggregated):
        return True
    if exit_code in _QUICK_REJECT_EXIT_CODES:
        return False
    if sandbox_type == "linux_seccomp" and exit_code == _SECCOMP_SIGSYS_EXIT_CODE:
        return True
    return False
