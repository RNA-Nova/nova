"""exec 策略裁决词汇（对位 codex execpolicy / approvals）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Decision(Enum):
    """裁决三档（对位 execpolicy Decision——Ord：Allow < Prompt < Forbidden）。

    聚合语义：多条规则命中时取 max（最严格者胜）。
    """

    ALLOW = "allow"
    PROMPT = "prompt"
    FORBIDDEN = "forbidden"

    def __lt__(self, other: Decision) -> bool:
        if not isinstance(other, Decision):
            return NotImplemented
        order = (Decision.ALLOW, Decision.PROMPT, Decision.FORBIDDEN)
        return order.index(self) < order.index(other)

    def __le__(self, other: Decision) -> bool:
        return self == other or self < other

    def __gt__(self, other: Decision) -> bool:
        return not self.__le__(other)

    def __ge__(self, other: Decision) -> bool:
        return not self.__lt__(other)

    @classmethod
    def parse(cls, raw: str) -> Decision:
        """字符串解析（对位 Decision::parse；非法值抛 ValueError）"""
        try:
            return cls(raw)
        except ValueError:
            raise ValueError(f"invalid decision: {raw}") from None


@dataclass(frozen=True)
class ExecPolicyAmendment:
    """“永远允许”的物化载体：一条命令前缀（对位 protocol ExecPolicyAmendment）。

    写回规则文件时序列化为 `prefix_rule(pattern=[...], decision="allow")`。
    """

    command: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("exec policy amendment requires a non-empty prefix")


class NetworkPolicyRuleAction(Enum):
    """网络规则动作（对位 protocol NetworkPolicyRuleAction）"""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class NetworkPolicyAmendment:
    """网络规则持久化（对位 protocol NetworkPolicyAmendment）"""

    host: str
    action: NetworkPolicyRuleAction
