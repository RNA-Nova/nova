"""规则语言（对位 codex `execpolicy/src/rule.rs`）：`prefix_rule`/`network_rule`
两类规则；`PrefixPattern` 前缀匹配（首 token 定值——按它索引规则——后续
PatternToken 单值/多选）。

match/not_match 样例不是规则状态（codex 的 `PrefixRule` 只有
pattern/decision/justification——样例是解析期校验件，验后即弃，见
parser.rs `pending_example_validations`），故不在本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..exec_server_verdict import Decision


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


def render_pattern_token(token: PatternToken) -> str:
    """对位 codex `render_pattern_token`：单值原样；多选渲染为 `"[a|b]"`
    单 token（前缀导出/展示场景不丢多选信息）"""
    if len(token.alternatives) == 1:
        return token.alternatives[0]
    return f"[{'|'.join(token.alternatives)}]"


# =============================================================================
# 规则本体
# =============================================================================


@dataclass(frozen=True)
class PrefixRule:
    """`prefix_rule(pattern, decision?, match?, not_match?, justification?)`

    对位 rule.rs `PrefixRule` 三字段形状——match/not_match 样例在解析期
    校验后丢弃（对位 parser.rs `pending_example_validations`），不随规则存留。
    """

    pattern: PrefixPattern
    decision: Decision = Decision.ALLOW
    justification: str | None = None

    def __post_init__(self) -> None:
        if self.justification is not None and not self.justification.strip():
            raise ValueError("justification cannot be empty")

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
                "network_rule protocol must be one of http, https, socks5_tcp, "
                f"socks5_udp (got {raw})"
            ) from None


def _is_ascii_digits(text: str) -> bool:
    """纯 ASCII 数字判定（对位 codex `chars().all(is_ascii_digit)`——
    `str.isdigit()` 会收非 ASCII 数字（如阿拉伯-印度数字），端口判定必须
    收紧，否则 py 剥落端口而 codex 保留整串为 host，双端归一结果分歧）"""
    return bool(text) and all("0" <= c <= "9" for c in text)


#: Rust `char::is_whitespace` 的码表（Unicode White_Space 属性全集 25 码点）。
#: Python `str.isspace()` 是它的超集——多收 U+001C–U+001F 四个分隔符；
#: 空白判定与裁剪都必须用同一张表，否则双端 accept/reject 与归一结果分歧
_RUST_WHITESPACE_CHARS = (
    "\t\n\x0b\x0c\r \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
_RUST_WHITESPACE = frozenset(_RUST_WHITESPACE_CHARS)


def _trim_whitespace(text: str) -> str:
    """White_Space 码表裁剪（对位 Rust `str::trim`）"""
    return text.strip(_RUST_WHITESPACE_CHARS)


def _to_ascii_lowercase(text: str) -> str:
    """ASCII-only 小写化（对位 Rust `to_ascii_lowercase`）——`str.lower()`
    是 Unicode 全量小写化（如 İ → "i̇" 两码点），双端归一结果会分叉"""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def normalize_network_rule_host(raw: str) -> str:
    """网络规则主机归一（对位 normalize_network_rule_host 逐条）：

    去空白/尾点/小写；剥 host:port 与 [v6]:port；拒绝空、含 scheme/path、
    通配符、空白字符。
    """
    host = _trim_whitespace(raw)
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
            if (
                not rest.startswith(":")
                or not rest[1:]
                or not _is_ascii_digits(rest[1:])
            ):
                raise ValueError(
                    f"network_rule host contains an unsupported suffix: {raw}"
                )
        host = inside
    elif host.count(":") == 1:
        candidate, _, port = host.rpartition(":")
        if candidate and port and _is_ascii_digits(port):
            host = candidate

    normalized = _to_ascii_lowercase(_trim_whitespace(host.rstrip(".")))
    if not normalized:
        raise ValueError("network_rule host cannot be empty")
    if "*" in normalized:
        raise ValueError(
            "network_rule host must be a specific host; wildcards are not allowed"
        )
    if any(c in _RUST_WHITESPACE for c in normalized):
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
