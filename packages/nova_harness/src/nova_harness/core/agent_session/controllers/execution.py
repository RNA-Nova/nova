"""执行审批与编排词汇（对位 codex core tools/sandboxing 与 otel）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from nova_exec_policy import ExecPolicyAmendment, NetworkPolicyAmendment


@dataclass(frozen=True)
class ExecApprovalRequirement:
    """编排层三态（对位 core ExecApprovalRequirement）。

    - Skip：无需审批（bypass_sandbox=True 表示政策明确放行——首次尝试可
      跳过沙箱）；proposed_amendment 为"沙箱失败后可提示用户豁免未来
      同类审批"的预生成写回候选。
    - NeedsApproval：需弹窗（reason 可空）；proposed_amendment 同上。
    - Forbidden：直接禁止（reason 必填）。
    """

    kind: str  # "skip" | "needs_approval" | "forbidden"
    reason: str | None = None
    bypass_sandbox: bool = False
    proposed_amendment: ExecPolicyAmendment | None = None

    @classmethod
    def skip(
        cls,
        *,
        bypass_sandbox: bool = False,
        proposed_amendment: ExecPolicyAmendment | None = None,
    ) -> ExecApprovalRequirement:
        return cls(
            kind="skip",
            bypass_sandbox=bypass_sandbox,
            proposed_amendment=proposed_amendment,
        )

    @classmethod
    def needs_approval(
        cls,
        *,
        reason: str | None = None,
        proposed_amendment: ExecPolicyAmendment | None = None,
    ) -> ExecApprovalRequirement:
        return cls(
            kind="needs_approval",
            reason=reason,
            proposed_amendment=proposed_amendment,
        )

    @classmethod
    def forbidden(cls, reason: str) -> ExecApprovalRequirement:
        if not reason:
            raise ValueError("forbidden requirement requires a reason")
        return cls(kind="forbidden", reason=reason)


class ReviewDecision(Enum):
    """审批弹窗结局（对位 protocol ReviewDecision；MCP 变体不属 exec 域不收）。

    - APPROVED：批准本次执行
    - APPROVED_EXECPOLICY_AMENDMENT：批准 + 应用 execpolicy 写回（未来同类放行）
    - APPROVED_FOR_SESSION：批准 + 本会话内同类自动批准
    - NETWORK_POLICY_AMENDMENT：持久化一条网络规则（allow/deny 按 host）
    - DENIED：拒绝执行，会话继续（带 rejection 文案——经 `with_rejection` 携带）
    - TIMED_OUT：自动审批评审超时（guardian 类）
    - ABORT：拒绝且中止当前回合直到用户下一指令
    """

    APPROVED = "approved"
    APPROVED_EXECPOLICY_AMENDMENT = "approved_execpolicy_amendment"
    APPROVED_FOR_SESSION = "approved_for_session"
    NETWORK_POLICY_AMENDMENT = "network_policy_amendment"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    ABORT = "abort"


@dataclass(frozen=True)
class ReviewDecisionPayload:
    """带载体的 ReviewDecision（amendment/rejection 附属数据的唯一通道）"""

    decision: ReviewDecision
    execpolicy_amendment: ExecPolicyAmendment | None = None
    network_policy_amendment: NetworkPolicyAmendment | None = None
    rejection: str | None = None

    def __post_init__(self) -> None:
        if self.decision is ReviewDecision.APPROVED_EXECPOLICY_AMENDMENT:
            if self.execpolicy_amendment is None:
                raise ValueError("execpolicy amendment variant requires an amendment")
        if self.decision is ReviewDecision.NETWORK_POLICY_AMENDMENT:
            if self.network_policy_amendment is None:
                raise ValueError(
                    "network policy amendment variant requires an amendment"
                )
        if self.decision is ReviewDecision.DENIED and not self.rejection:
            raise ValueError("denied variant requires a rejection message")


class ToolDecisionSource(Enum):
    """决策来源 tag（对位 otel ToolDecisionSource——遥测/审计挂点）"""

    #: 自动评审器（guardian 类自动审批流）
    AUTOMATED_REVIEWER = "automated_reviewer"
    #: 配置/规则（规则文件命中）
    CONFIG = "config"
    #: 用户（弹窗当场决定；本会话缓存命中追溯归属同 USER）
    USER = "user"
