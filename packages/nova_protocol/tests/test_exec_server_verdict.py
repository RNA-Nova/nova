"""exec_server_verdict 测试——裁决词汇语义 pin（对位 codex 形状）"""

import pytest
from nova_protocol.exec_server_verdict import (
    Decision,
    ExecApprovalRequirement,
    ExecPolicyAmendment,
    NetworkPolicyAmendment,
    NetworkPolicyRuleAction,
    ReviewDecision,
    ReviewDecisionPayload,
)


def test_decision_ordering():
    assert Decision.ALLOW < Decision.PROMPT < Decision.FORBIDDEN
    assert (
        max(Decision.ALLOW, Decision.PROMPT, Decision.FORBIDDEN) is Decision.FORBIDDEN
    )


def test_decision_parse():
    assert Decision.parse("allow") is Decision.ALLOW
    assert Decision.parse("prompt") is Decision.PROMPT
    assert Decision.parse("forbidden") is Decision.FORBIDDEN
    with pytest.raises(ValueError):
        Decision.parse("maybe")


def test_amendment_requires_non_empty_prefix():
    with pytest.raises(ValueError):
        ExecPolicyAmendment(command=())


def test_requirement_factories():
    skip = ExecApprovalRequirement.skip(bypass_sandbox=True)
    assert skip.kind == "skip" and skip.bypass_sandbox is True
    needs = ExecApprovalRequirement.needs_approval(reason="dangerous command")
    assert needs.kind == "needs_approval" and needs.reason == "dangerous command"
    forbidden = ExecApprovalRequirement.forbidden("policy forbidden")
    assert forbidden.kind == "forbidden" and forbidden.reason == "policy forbidden"
    with pytest.raises(ValueError):
        ExecApprovalRequirement.forbidden("")


def test_review_decision_payload_variants():
    amendment = ExecPolicyAmendment(command=("git", "push"))
    payload = ReviewDecisionPayload(
        decision=ReviewDecision.APPROVED_EXECPOLICY_AMENDMENT,
        execpolicy_amendment=amendment,
    )
    assert payload.execpolicy_amendment.command == ("git", "push")

    network = ReviewDecisionPayload(
        decision=ReviewDecision.NETWORK_POLICY_AMENDMENT,
        network_policy_amendment=NetworkPolicyAmendment(
            host="example.com", action=NetworkPolicyRuleAction.ALLOW
        ),
    )
    assert network.network_policy_amendment.host == "example.com"

    denied = ReviewDecisionPayload(decision=ReviewDecision.DENIED, rejection="no")
    assert denied.rejection == "no"


def test_review_decision_payload_invariants():
    with pytest.raises(ValueError):
        ReviewDecisionPayload(decision=ReviewDecision.APPROVED_EXECPOLICY_AMENDMENT)
    with pytest.raises(ValueError):
        ReviewDecisionPayload(decision=ReviewDecision.DENIED)
