"""求值（对位 codex `execpolicy/src/policy.rs`）：`MatchOptions`、规则命中
（对位 `RuleMatch` 枚举）、`Evaluation`、`Policy`。

求值链（对位 policy.rs:308-360 `matches_for_command_with_options`）：
先 `match_exact_rules`（字面 cmd[0] 索引）；空且 `resolve_host_executables`
才 `match_host_executable_rules`（host 可执行解析）；再空交
heuristics_fallback（危险初判）。**无命中且无兜底时 decision=None**（对位
codex：`check` 的 fallback 是必传参数，`Evaluation::from_matches` 有非空
不变式——绝不凭空造 ALLOW；只看 `matched_rules` 的无兜底调用方对位 codex
`matches_for_command` 用法）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..exec_server_verdict import Decision
from .executable_name import executable_path_lookup_key
from .rule import NetworkRule, PrefixRule, render_pattern_token


@dataclass(frozen=True)
class MatchOptions:
    """匹配选项（对位 policy.rs `MatchOptions`——`#[derive(Default)]`：
    默认**不**解析 host 可执行，裸 `check` 只做字面精确匹配）"""

    resolve_host_executables: bool = False


@dataclass(frozen=True)
class PrefixRuleMatch:
    """prefix 规则命中（对位 rule.rs `RuleMatch::PrefixRuleMatch` 四字段形状）。

    - `resolved_program`：host 可执行解析命中的原始绝对路径（对位
      `with_resolved_program` 一案——精确匹配命中恒为 None）；
    - `justification`：随命中克隆的规则理由（对位 rule.rs `matches` 的
      `justification: self.justification.clone()`）。
    """

    matched_prefix: tuple[str, ...]
    decision: Decision
    resolved_program: str | None = None
    justification: str | None = None

    def with_resolved_program(self, resolved_program: str) -> PrefixRuleMatch:
        """对位 `RuleMatch::with_resolved_program`（其余字段原样保留）"""
        return replace(self, resolved_program=resolved_program)


@dataclass(frozen=True)
class HeuristicsRuleMatch:
    """启发式兜底命中（非规则——is_match() 判假，对位 RuleMatch::Heuristics）。

    `command` 携带触发兜底的命令词（对位 Rust 同名字段——裁决装配件靠它
    产出"永远允许"的写回候选，见上游 try_derive_execpolicy_amendment_*）。
    """

    decision: Decision
    command: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Evaluation:
    """求值结果：决策 = 全部命中的最严格档（max）。

    `decision=None` 表示无规则命中且未咨询兜底（对位 codex
    `Evaluation::from_matches` 的非空不变式——codex 的 `check` 强制携带
    兜底，永不落到空命中；本移植允许省兜底，但空命中只给 None，不凭空
    造 ALLOW 架空裁决）。有兜底或任一命中时恒非 None。
    """

    decision: Decision | None
    matched_rules: tuple[PrefixRuleMatch | HeuristicsRuleMatch, ...]

    def is_match(self) -> bool:
        """是否规则命中（启发式兜底不算规则命中）"""
        return any(isinstance(m, PrefixRuleMatch) for m in self.matched_rules)


class Policy:
    """规则集：prefix 规则按 program 索引 + 网络规则 + host 可执行路径映射
    （对位 policy.rs `Policy::from_parts` 三分量）"""

    def __init__(
        self,
        rules_by_program: dict[str, list[PrefixRule]] | None = None,
        network_rules: list[NetworkRule] | None = None,
        host_executables_by_name: dict[str, list[str]] | None = None,
    ) -> None:
        self._rules_by_program = rules_by_program or {}
        self._network_rules = network_rules or []
        self._host_executables_by_name = host_executables_by_name or {}

    @classmethod
    def empty(cls) -> Policy:
        return cls()

    @property
    def rules_by_program(self) -> dict[str, list[PrefixRule]]:
        return self._rules_by_program

    @property
    def network_rules(self) -> list[NetworkRule]:
        return self._network_rules

    def host_executables(self) -> dict[str, list[str]]:
        """host 可执行声明映射（对位 policy.rs `host_executables()`——
        裸名 → 已声明绝对路径表；只读视图，同 rules_by_program 惯例）"""
        return self._host_executables_by_name

    def add_prefix_rule(self, rule: PrefixRule) -> None:
        self._rules_by_program.setdefault(rule.program, []).append(rule)

    def set_host_executable_paths(self, name: str, paths: list[str]) -> None:
        """注册 host 可执行路径表（对位 policy.rs `set_host_executable_paths`
        ——insert 语义：后定义覆盖先定义）"""
        self._host_executables_by_name[name] = list(paths)

    def get_allowed_prefixes(self) -> list[tuple[str, ...]]:
        """全部 allow 规则的前缀（对位 get_allowed_prefixes）：多选 token 经
        `render_pattern_token` 渲染为 `"[a|b]"` 单 token（不丢信息），输出
        排序去重（codex 的 sort()+dedup()）"""
        out: list[tuple[str, ...]] = []
        for rules in self._rules_by_program.values():
            for rule in rules:
                if rule.decision is Decision.ALLOW:
                    out.append(
                        (rule.pattern.first,)
                        + tuple(render_pattern_token(t) for t in rule.pattern.rest)
                    )
        return sorted(set(out))

    def compiled_network_domains(self) -> tuple[list[str], list[str]]:
        """网络规则编译为 (allowed, denied) 域名单（对位 policy.rs
        `compiled_network_domains`）：按规则顺序——allow 先从 denied 移除该
        host 再 upsert 进 allowed；forbidden 对称；prompt 跳过。"""
        allowed: list[str] = []
        denied: list[str] = []
        for rule in self._network_rules:
            if rule.decision is Decision.ALLOW:
                denied = [entry for entry in denied if entry != rule.host]
                _upsert_domain(allowed, rule.host)
            elif rule.decision is Decision.FORBIDDEN:
                allowed = [entry for entry in allowed if entry != rule.host]
                _upsert_domain(denied, rule.host)
            # Decision.PROMPT：跳过（对位 policy.rs 空分支）
        return allowed, denied

    def check(
        self,
        cmd: tuple[str, ...] | list[str],
        heuristics_fallback=None,
    ) -> Evaluation:
        """对一条命令求值（对位 policy.rs `check`——裸 check =
        `check_with_options(MatchOptions::default())`，**不解析** host 可执行；
        查找键为字面 ``cmd[0]``，路径/裸名互不认。可执行名归一归
        ``check_with_options`` + ``host_executable`` 声明族）。

        无命中且未给兜底（或兜底返回 None）时 ``decision=None``（对位 codex
        的非空命中不变式——无兜底调用方应只读 ``matched_rules``，对位
        codex `matches_for_command` 用法），不凭空造 ALLOW。
        """
        return self.check_with_options(cmd, heuristics_fallback, MatchOptions())

    def check_with_options(
        self,
        cmd: tuple[str, ...] | list[str],
        heuristics_fallback=None,
        options: MatchOptions | None = None,
    ) -> Evaluation:
        """带匹配选项求值（对位 policy.rs `check_with_options`）：
        全部规则匹配 + 兜底 → Evaluation（decision 取 max）"""
        matched = self.matches_for_command_with_options(
            cmd, heuristics_fallback, options
        )
        if not matched:
            return Evaluation(decision=None, matched_rules=())
        decision = max((m.decision for m in matched), key=_decision_rank)
        return Evaluation(decision=decision, matched_rules=tuple(matched))

    def check_multiple(
        self, commands: list[tuple[str, ...]], heuristics_fallback=None
    ) -> Evaluation:
        """多命令聚合求值（复合命令逐段——对位 check_multiple =
        check_multiple_with_options 默认 options）；全部段都无命中且无兜底时
        ``decision=None``（同 check 的无兜底纪律）"""
        return self.check_multiple_with_options(
            commands, heuristics_fallback, MatchOptions()
        )

    def check_multiple_with_options(
        self,
        commands: list[tuple[str, ...]],
        heuristics_fallback=None,
        options: MatchOptions | None = None,
    ) -> Evaluation:
        """带匹配选项的多命令聚合（对位 policy.rs
        `check_multiple_with_options`）"""
        all_matched: list[PrefixRuleMatch | HeuristicsRuleMatch] = []
        for cmd in commands:
            all_matched.extend(
                self.matches_for_command_with_options(cmd, heuristics_fallback, options)
            )
        if not all_matched:
            return Evaluation(decision=None, matched_rules=())
        decision = max((m.decision for m in all_matched), key=_decision_rank)
        return Evaluation(decision=decision, matched_rules=tuple(all_matched))

    def matches_for_command(
        self,
        cmd: tuple[str, ...] | list[str],
        heuristics_fallback=None,
    ) -> list[PrefixRuleMatch | HeuristicsRuleMatch]:
        """对位 policy.rs `matches_for_command`（默认 options——不解析）"""
        return self.matches_for_command_with_options(
            cmd, heuristics_fallback, MatchOptions()
        )

    def matches_for_command_with_options(
        self,
        cmd: tuple[str, ...] | list[str],
        heuristics_fallback=None,
        options: MatchOptions | None = None,
    ) -> list[PrefixRuleMatch | HeuristicsRuleMatch]:
        """规则匹配主链（对位 policy.rs `matches_for_command_with_options`）：
        先精确规则；空且 `resolve_host_executables` 才走 host 可执行解析；
        再空交启发式兜底（兜底返回 None 视为无兜底命中）。"""
        options = options or MatchOptions()
        cmd = tuple(cmd)
        matched: list[PrefixRuleMatch | HeuristicsRuleMatch] = self._match_exact_rules(
            cmd
        )
        if not matched and options.resolve_host_executables:
            matched = self._match_host_executable_rules(cmd)
        if not matched and heuristics_fallback is not None:
            decision = heuristics_fallback(cmd)
            if decision is not None:
                matched = [HeuristicsRuleMatch(decision=decision, command=cmd)]
        return matched

    def _match_exact_rules(
        self, cmd: tuple[str, ...]
    ) -> list[PrefixRuleMatch | HeuristicsRuleMatch]:
        """字面 cmd[0] 索引的精确规则匹配（对位 policy.rs `match_exact_rules`
        ——codex 返回 `Vec<RuleMatch>`，命中负载实为 PrefixRuleMatch 一族）"""
        program = cmd[0] if cmd else None
        if program is None:
            return []
        matched: list[PrefixRuleMatch | HeuristicsRuleMatch] = []
        for rule in self._rules_by_program.get(program, []):
            prefix = rule.pattern.matches_prefix(cmd)
            if prefix is not None:
                matched.append(_prefix_rule_match(rule, prefix))
        return matched

    def _match_host_executable_rules(
        self, cmd: tuple[str, ...]
    ) -> list[PrefixRuleMatch | HeuristicsRuleMatch]:
        """host 可执行解析匹配（对位 policy.rs `match_host_executable_rules`）：

        cmd[0] 必须可解析为绝对路径 → 取 basename 作查找键 →
        `host_executables_by_name` 有声明则 cmd[0] 必须在声明 paths 内
        （显式空 allowlist → 不命中；**无声明则放行** basename 匹配）→
        用 `(basename, *cmd[1:])` 匹配规则 → 每个命中
        `with_resolved_program(cmd[0])`。
        """
        first = cmd[0] if cmd else None
        if first is None:
            return []
        # 对位 `AbsolutePathBuf::try_from(first)`：非绝对路径不解析
        # （posix 绝对路径 = 以 "/" 开头）
        if not first.startswith("/"):
            return []
        basename = executable_path_lookup_key(first)
        if basename is None:
            return []
        rules = self._rules_by_program.get(basename)
        if not rules:
            return []
        declared = self._host_executables_by_name.get(basename)
        if declared is not None and first not in declared:
            return []
        basename_command = (basename, *cmd[1:])
        matched: list[PrefixRuleMatch | HeuristicsRuleMatch] = []
        for rule in rules:
            prefix = rule.pattern.matches_prefix(basename_command)
            if prefix is not None:
                matched.append(
                    _prefix_rule_match(rule, prefix).with_resolved_program(first)
                )
        return matched


def _prefix_rule_match(rule: PrefixRule, prefix: tuple[str, ...]) -> PrefixRuleMatch:
    """规则命中构造（对位 rule.rs `PrefixRule::matches`——justification
    随命中克隆）"""
    return PrefixRuleMatch(
        matched_prefix=prefix,
        decision=rule.decision,
        justification=rule.justification,
    )


def _upsert_domain(entries: list[str], host: str) -> None:
    """对位 policy.rs `upsert_domain`：先去重（全部移除同值）再尾部追加"""
    entries[:] = [entry for entry in entries if entry != host]
    entries.append(host)


def _decision_rank(decision: Decision) -> int:
    return (Decision.ALLOW, Decision.PROMPT, Decision.FORBIDDEN).index(decision)
