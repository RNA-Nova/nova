"""npm semver 子集：版本解析 / 比较 / range 匹配（纯 Python，零第三方依赖）。

不复用 ``packaging``（PEP 440）：npm semver 与 PEP 440 的 prerelease 记法、
range 语法与 0.x 锁定规则均不兼容。

支持的 spec 形态（对齐 npm semver 语义的实用子集）：

- 精确版本：``1.2.3`` / ``1.2.3-beta.1`` / ``1.2.3+build`` / ``v1.2.3``
  （``v``/``V`` 前缀容忍归一；build metadata 解析时接受但不参与 precedence）；
- 任意版本：``*`` / ``x`` / ``X``（无界区间，max satisfying 取最高非
  prerelease 版本）；
- x-range / 通配段 / 裸部分版本：``1.2.x`` / ``1.2.*`` → ``>=1.2.0 <1.3.0``；
  ``1.x`` → ``>=1.0.0 <2.0.0``；裸部分版本 ``1`` ≡ ``1.x``、``1.2`` ≡ ``1.2.x``；
- caret range：``^1.2.3`` → ``>=1.2.3 <2.0.0``；``^0.2.3`` → ``>=0.2.3 <0.3.0``；
  ``^0.0.3`` → ``>=0.0.3 <0.0.4``（0.x 锁定最左非零位）；
  ``^1`` / ``^1.2`` / ``^1.2.x`` → ``>=1.2.0 <2.0.0``；``^*`` = 任意版本；
- tilde range：``~1.2.3`` → ``>=1.2.3 <1.3.0``；``~1.2`` / ``~1.2.x`` →
  ``>=1.2.0 <1.3.0``；``~1`` → ``>=1.0.0 <2.0.0``；``~*`` = 任意版本；
- 比较器集（空格分隔，AND 交集）：``>=1.2.0 <2.0.0`` / ``>1.0.0 <=1.5.0`` /
  ``=1.2.3``（``=`` 即精确钉版）；比较器可混部分版本，按 npm 规则展开——
  ``>=1.2`` := ``>=1.2.0``；``<1.2`` := ``<1.2.0``（不含 1.2.0 自身）；
  ``>1.2`` := ``>=1.3.0``；``<=1.2`` := ``<1.3.0``；``=1.2`` := ``>=1.2.0 <1.3.0``；
- 并集：``^1.2.0 || ^2.0.0``——任一成员区间满足即放行；``||`` 分支的空串
  按 npm 语义视为任意版本；
- hyphen range：``1.2.3 - 2.3.4`` := ``>=1.2.3 <=2.3.4``（连字符两侧必须有
  空格，与 prerelease 连字符区分）；部分版本端点按 npm 规则展开——
  ``1.2 - 2.3`` := ``>=1.2.0 <2.4.0``（左端点补零取闭下界、右端点上界提升
  取开上界）。hyphen 独占整个比较器集，不支持与其他比较器混排。

所有 range 形态统一归一为 ``NpmRange`` 区间（``||`` 为 ``NpmRangeUnion``），
匹配入口为 ``allows()`` / ``max_satisfying()``。

prerelease 参与规则（npm 规则的简化版）：range 匹配默认排除带 prerelease
的候选版本；仅当 spec 自身携带 prerelease（如 ``^1.2.3-beta`` /
``>=1.2.3-beta``）时，放行与其同 ``[major, minor, patch]`` 三元组的
prerelease 候选（比较器集 / hyphen 含多个 prerelease 版本时只记录首个三元组
——实用化简化）。精确版本指定 prerelease 不走 range 匹配（调用方按原逻辑
直取）。

不在子集内的语法（dist-tag 引用如 ``beta``、URL 依赖等）在解析期抛
``ValueError``，由调用方包装为用户可读的安装错误。
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple, Union

# 完整版本：major.minor.patch[-prerelease][+build]（prerelease/build 段为点分标识符）
_VERSION_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

# spec 版本体（比较器 / x-range / ^~ / hyphen 端点共用）：段为数字或通配符
# （x/X/*），容忍单个 v/V 前缀，可带 prerelease/build（仅完整版本允许）
_BODY_RE = re.compile(
    r"^[vV]?"
    r"(\d+|[xX*])"
    r"(?:\.(\d+|[xX*]))?"
    r"(?:\.(\d+|[xX*]))?"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)

# hyphen range：``A - B``（连字符两侧必须有空格，借以区分 prerelease 连字符）
_HYPHEN_RE = re.compile(r"^(.+?)\s+-\s+(.+)$")

# 比较器操作符前缀（长前缀优先匹配）
_COMPARATOR_OPS = ("<=", ">=", "<", ">", "=")


def _compare_prerelease(left: Tuple[str, ...], right: Tuple[str, ...]) -> int:
    """prerelease 标识符逐段比较，返回 -1/0/1（npm 规则）。

    纯数字段按数值比较；含字母/连字符的段按 ASCII 字典序；数字段优先级
    低于非数字段；逐段相同则字段多者更高。
    """
    for a, b in zip(left, right):
        if a == b:
            continue
        a_numeric, b_numeric = a.isdigit(), b.isdigit()
        if a_numeric and b_numeric:
            return -1 if int(a) < int(b) else 1
        if a_numeric:
            return -1
        if b_numeric:
            return 1
        return -1 if a < b else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


@functools.total_ordering
@dataclass(frozen=True)
class Version:
    """npm semver 版本号（不可变值对象；build metadata 已在解析期丢弃）。"""

    major: int
    minor: int
    patch: int
    prerelease: Tuple[str, ...] = ()  # 空 = 正式版

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        return text

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        self_core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if self_core != other_core:
            return self_core < other_core
        # 同一三元组：带 prerelease 的版本低于正式版
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        return _compare_prerelease(self.prerelease, other.prerelease) < 0


def _version_from_match(match: "re.Match[str]") -> Version:
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    return Version(
        major=int(match.group(1)),
        minor=int(match.group(2)),
        patch=int(match.group(3)),
        prerelease=prerelease,
    )


def parse_version(text: str) -> Version:
    """解析完整版本号（``1.2.3[-prerelease][+build]``）；不合法抛 ``ValueError``。"""
    match = _VERSION_RE.match(text)
    if not match:
        raise ValueError(f"invalid npm version: {text!r}")
    return _version_from_match(match)


@dataclass(frozen=True)
class NpmRange:
    """range 归一化后的区间（单一 range 形态 / ``||`` 单侧的匹配单元）。

    ``lower`` / ``upper`` 为 ``None`` 表示无界（如 ``*`` / ``>=1.0.0``）；
    边界开闭由 ``lower_inclusive`` / ``upper_inclusive`` 控制（默认下闭
    上开——``^``/``~``/x-range 的形态；``>``/``<=``/hyphen 右端点等会翻转）。
    比较器集交集为空（如 ``>2.0.0 <1.0.0``）不在解析期报错，``allows``
    恒为 False（对齐 npm 行为）。

    ``prerelease_base``：spec 自身携带 prerelease 时记录其
    ``(major, minor, patch)`` 三元组——匹配时仅放行同三元组的 prerelease
    候选；为 ``None`` 时排除一切 prerelease 候选。
    """

    lower: Optional[Version]  # 下界（None = 无下界）
    upper: Optional[Version]  # 上界（None = 无上界）
    prerelease_base: Optional[Tuple[int, int, int]] = None
    lower_inclusive: bool = True
    upper_inclusive: bool = False

    def allows(self, version: Version) -> bool:
        """*version* 是否落在区间内（含 prerelease 参与规则）。"""
        if version.prerelease:
            if self.prerelease_base is None:
                return False
            if (version.major, version.minor, version.patch) != self.prerelease_base:
                return False
        if self.lower is not None and (
            version < self.lower or (version == self.lower and not self.lower_inclusive)
        ):
            return False
        if self.upper is not None and (
            version > self.upper or (version == self.upper and not self.upper_inclusive)
        ):
            return False
        return True


@dataclass(frozen=True)
class NpmRangeUnion:
    """``||`` 并集：任一成员区间满足即放行。"""

    ranges: Tuple[NpmRange, ...]

    def allows(self, version: Version) -> bool:
        """*version* 是否被任一成员区间放行（含 prerelease 参与规则）。"""
        return any(member.allows(version) for member in self.ranges)


# 无界区间（``*`` / ``x`` / 空比较器集）
_ANY_RANGE = NpmRange(lower=None, upper=None)

# 恒假区间（``>*`` / ``<*`` 等——npm 语义为不匹配任何版本）
_EMPTY_RANGE = NpmRange(lower=Version(0, 0, 0), upper=Version(0, 0, 0))


@dataclass(frozen=True)
class _VersionBody:
    """spec 版本体的解析结果；通配/缺省段为 ``None``。"""

    major: Optional[int]
    minor: Optional[int]
    patch: Optional[int]
    prerelease: Tuple[str, ...] = ()

    @property
    def is_any(self) -> bool:
        """``*`` / ``x`` 等全通配：不施加任何约束。"""
        return self.major is None

    @property
    def is_full(self) -> bool:
        """三段齐全（可带 prerelease/build）。"""
        return self.patch is not None

    def zero_filled(self) -> Version:
        """缺省段补零后的版本（下界 / 钉版用；调用方保证非全通配）。"""
        return Version(
            self.major if self.major is not None else 0,
            self.minor if self.minor is not None else 0,
            self.patch if self.patch is not None else 0,
            self.prerelease,
        )


def _parse_body(text: str, context: str) -> _VersionBody:
    """解析 spec 版本体（数字/通配段 + 可选 v 前缀 + 可选 prerelease/build）。

    *context* 为完整 spec 文本，仅用于报错信息。通配段之后不允许再出现
    数字段（``1.x.3`` 非法）；部分/通配版本不允许携带 prerelease/build。
    """
    match = _BODY_RE.match(text)
    if not match:
        raise ValueError(f"invalid npm version spec: {context!r}")

    def _segment(raw: Optional[str]) -> Optional[int]:
        if raw is None or raw in ("x", "X", "*"):
            return None
        return int(raw)

    major = _segment(match.group(1))
    minor = _segment(match.group(2))
    patch = _segment(match.group(3))
    seen_wildcard = False
    for segment in (major, minor, patch):
        if segment is None:
            seen_wildcard = True
        elif seen_wildcard:
            raise ValueError(f"invalid npm version spec: {context!r}")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    if (match.group(4) or match.group(5)) and patch is None:
        # npm 不允许部分/通配版本携带 prerelease/build（``1.2-beta`` / ``1.2.x-beta``）
        raise ValueError(f"invalid npm version spec: {context!r}")
    return _VersionBody(major=major, minor=minor, patch=patch, prerelease=prerelease)


def _prerelease_base_of(body: _VersionBody) -> Optional[Tuple[int, int, int]]:
    """body 携带 prerelease 时返回其三元组（此时必为完整版本——解析期已校验）。"""
    if not body.prerelease:
        return None
    # 类型上 Optional 收窄不了；运行时保证三段均为 int（prerelease 仅限完整版本）
    return (body.major, body.minor, body.patch)  # type: ignore[return-value]


def _tilde_upper(major: int, minor: Optional[int]) -> Version:
    """tilde / x-range / 部分版本共用的上界（不含）：提升最后指定位的下一位。

    ``~1.2.3`` / ``~1.2`` / ``1.2.x`` → ``<major.(minor+1).0``；
    ``~1`` / ``1.x`` → ``<(major+1).0.0``。
    """
    if minor is None:
        return Version(major + 1, 0, 0)
    return Version(major, minor + 1, 0)


def _caret_upper(major: int, minor: Optional[int], patch: Optional[int]) -> Version:
    """caret range 上界（不含）：锁定最左非零位，其后位清零。

    - ``^1.2.3`` → ``<2.0.0``；``^1.2`` / ``^1.2.x`` / ``^1`` 同理（``<2.0.0``）
    - ``^0.2.3`` / ``^0.2`` → ``<0.3.0``
    - ``^0.0.3`` → ``<0.0.4``；``^0.0`` → ``<0.1.0``；``^0`` → ``<1.0.0``
    """
    if major > 0 or minor is None:
        return Version(major + 1, 0, 0)
    if minor > 0:
        return Version(0, minor + 1, 0)
    if patch is not None:
        return Version(0, 0, patch + 1)
    return Version(0, 1, 0)


def _pin_range(version: Version) -> NpmRange:
    """精确钉版区间：``=1.2.3`` / 并集分支中的裸完整版本（上下界同值、双侧含）。"""
    prerelease_base = (
        (version.major, version.minor, version.patch) if version.prerelease else None
    )
    return NpmRange(
        lower=version,
        upper=version,
        prerelease_base=prerelease_base,
        lower_inclusive=True,
        upper_inclusive=True,
    )


def _comparator_range(op: str, body: _VersionBody) -> NpmRange:
    """单个比较器（op + 版本体）脱糖为区间。

    部分/通配版本按 npm 规则展开：``<1.2`` := ``<1.2.0``（不含 1.2.0 自身）；
    ``<=1.2`` := ``<1.3.0``；``>1.2`` := ``>=1.3.0``；``>=1.2`` := ``>=1.2.0``；
    ``=1.2`` / 裸部分版本 := ``>=1.2.0 <1.3.0``（x-range 语义）。
    """
    if body.is_any:
        # npm：``>*`` / ``<*`` 不匹配任何版本；其余 op 遇全通配不施加约束
        return _EMPTY_RANGE if op in (">", "<") else _ANY_RANGE
    if op in ("", "="):
        if body.is_full:
            return _pin_range(body.zero_filled())
        # 裸部分版本 / ``=1.2``：x-range 展开（``1`` ≡ ``1.x``，``1.2`` ≡ ``1.2.x``）
        return NpmRange(
            lower=body.zero_filled(), upper=_tilde_upper(body.major, body.minor)  # type: ignore[arg-type]
        )
    if op == ">=":
        return NpmRange(
            lower=body.zero_filled(),
            upper=None,
            prerelease_base=_prerelease_base_of(body),
        )
    if op == ">":
        if body.is_full:
            return NpmRange(
                lower=body.zero_filled(),
                upper=None,
                prerelease_base=_prerelease_base_of(body),
                lower_inclusive=False,
            )
        # ``>1.2`` := ``>=1.3.0``（跳过整个 1.2.x 段）
        return NpmRange(
            lower=_tilde_upper(body.major, body.minor),  # type: ignore[arg-type]
            upper=None,
        )
    if op == "<=":
        if body.is_full:
            return NpmRange(
                lower=None,
                upper=body.zero_filled(),
                prerelease_base=_prerelease_base_of(body),
                upper_inclusive=True,
            )
        # ``<=1.2`` := ``<1.3.0``（覆盖整个 1.2.x 段）
        return NpmRange(
            lower=None, upper=_tilde_upper(body.major, body.minor)  # type: ignore[arg-type]
        )
    # op == "<"
    if body.is_full:
        return NpmRange(
            lower=None,
            upper=body.zero_filled(),
            prerelease_base=_prerelease_base_of(body),
        )
    # ``<1.2`` := ``<1.2.0``（不含 1.2.0 自身）
    return NpmRange(lower=None, upper=body.zero_filled())


def _caret_tilde_range(op: str, body: _VersionBody) -> NpmRange:
    """``^``/``~`` 前缀 range（版本体可含通配/部分段；``^*`` / ``~x`` = 任意）。"""
    if body.is_any:
        return _ANY_RANGE
    upper = (
        _caret_upper(body.major, body.minor, body.patch)  # type: ignore[arg-type]
        if op == "^"
        else _tilde_upper(body.major, body.minor)  # type: ignore[arg-type]
    )
    return NpmRange(
        lower=body.zero_filled(),
        upper=upper,
        prerelease_base=_prerelease_base_of(body),
    )


def _hyphen_range(from_text: str, to_text: str, context: str) -> NpmRange:
    """hyphen range ``A - B`` := ``>=A <=B``；部分版本端点按 npm 规则展开。

    左端点部分版本补零取闭下界；右端点部分版本上界提升取开上界
    （``1.2 - 2.3`` := ``>=1.2.0 <2.4.0``）。
    """
    from_body = _parse_body(from_text, context)
    to_body = _parse_body(to_text, context)
    lower: Optional[Version] = None if from_body.is_any else from_body.zero_filled()
    upper: Optional[Version]
    upper_inclusive = False
    if to_body.is_any:
        upper = None
    elif to_body.is_full:
        upper = to_body.zero_filled()
        upper_inclusive = True  # 完整右端点闭区间（``<=2.3.4``）
    else:
        upper = _tilde_upper(to_body.major, to_body.minor)  # type: ignore[arg-type]
    prerelease_base = _prerelease_base_of(from_body) or _prerelease_base_of(to_body)
    return NpmRange(
        lower=lower,
        upper=upper,
        prerelease_base=prerelease_base,
        lower_inclusive=True,
        upper_inclusive=upper_inclusive,
    )


def _intersect(left: NpmRange, right: NpmRange) -> NpmRange:
    """两个区间的交集（比较器集 AND 语义；可能得到恒假空区间）。"""
    if left.lower is None:
        lower, lower_inclusive = right.lower, right.lower_inclusive
    elif right.lower is None or left.lower > right.lower:
        lower, lower_inclusive = left.lower, left.lower_inclusive
    elif right.lower > left.lower:
        lower, lower_inclusive = right.lower, right.lower_inclusive
    else:  # 下界同值：任一侧 exclusive 则结果 exclusive
        lower = left.lower
        lower_inclusive = left.lower_inclusive and right.lower_inclusive
    if left.upper is None:
        upper, upper_inclusive = right.upper, right.upper_inclusive
    elif right.upper is None or left.upper < right.upper:
        upper, upper_inclusive = left.upper, left.upper_inclusive
    elif right.upper < left.upper:
        upper, upper_inclusive = right.upper, right.upper_inclusive
    else:  # 上界同值：同理
        upper = left.upper
        upper_inclusive = left.upper_inclusive and right.upper_inclusive
    prerelease_base = (
        left.prerelease_base
        if left.prerelease_base is not None
        else right.prerelease_base
    )
    return NpmRange(
        lower=lower,
        upper=upper,
        prerelease_base=prerelease_base,
        lower_inclusive=lower_inclusive,
        upper_inclusive=upper_inclusive,
    )


def _parse_comparator_set(text: str) -> Union[Version, NpmRange]:
    """解析 ``||`` 单侧：hyphen range 或空格分隔的比较器集（AND 交集）。

    单个裸完整版本（可带 ``v`` 前缀）保持 ``Version`` 契约返回——调用方
    依赖该类型走精确版本直取路径；其余形态一律归一为 ``NpmRange``。
    """
    stripped = text.strip()
    if not stripped:
        # npm：空比较器集 = 任意版本（``||`` 分支空串同理）
        return _ANY_RANGE
    hyphen = _HYPHEN_RE.match(stripped)
    if hyphen:
        return _hyphen_range(hyphen.group(1), hyphen.group(2), stripped)
    tokens = stripped.split()
    result: Optional[NpmRange] = None
    for index, token in enumerate(tokens):
        if token[0] in ("^", "~"):
            fragment = _caret_tilde_range(token[0], _parse_body(token[1:], stripped))
        else:
            op = ""
            body_text = token
            for candidate in _COMPARATOR_OPS:
                if token.startswith(candidate):
                    op = candidate
                    body_text = token[len(candidate) :]
                    break
            body = _parse_body(body_text, stripped)
            if len(tokens) == 1 and op == "" and body.is_full:
                # 裸完整版本 → 精确 Version（resolver 直取，不走 range 匹配）
                return body.zero_filled()
            fragment = _comparator_range(op, body)
        result = fragment if result is None else _intersect(result, fragment)
    assert result is not None  # tokens 非空
    return result


def parse_version_spec(text: str) -> Union[Version, NpmRange, NpmRangeUnion]:
    """解析 npm 版本 spec。

    精确版本返回 ``Version``；range / 部分版本 / 通配返回 ``NpmRange``；
    ``||`` 并集返回 ``NpmRangeUnion``。非法语法抛 ``ValueError``。
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty npm version spec")
    if "||" not in stripped:
        return _parse_comparator_set(stripped)
    branches = tuple(_parse_comparator_set(part) for part in stripped.split("||"))
    # 并集分支中的裸精确版本按单点区间参与
    return NpmRangeUnion(
        ranges=tuple(
            branch if isinstance(branch, NpmRange) else _pin_range(branch)
            for branch in branches
        )
    )


def max_satisfying(
    candidates: Iterable[str], range_spec: Union[NpmRange, NpmRangeUnion]
) -> Optional[str]:
    """从 *candidates*（版本字符串）中选出满足 *range_spec* 的最高版本。

    无法解析为合法 semver 的候选直接跳过；按 semver precedence 比较
    （非字典序）；无满足版本返回 ``None``。
    """
    best_key: Optional[str] = None
    best: Optional[Version] = None
    for key in candidates:
        try:
            version = parse_version(key)
        except ValueError:
            continue
        if not range_spec.allows(version):
            continue
        if best is None or version > best:
            best = version
            best_key = key
    return best_key


__all__ = [
    "NpmRange",
    "NpmRangeUnion",
    "Version",
    "max_satisfying",
    "parse_version",
    "parse_version_spec",
]
