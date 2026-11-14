"""exec 策略裁决词汇语义 pin（对位 codex 形状）"""

import pytest
from nova_exec_policy import Decision, ExecPolicyAmendment


def test_decision_ordering():
    assert Decision.ALLOW < Decision.PROMPT < Decision.FORBIDDEN
    assert max(Decision.ALLOW, Decision.PROMPT, Decision.FORBIDDEN) is Decision.FORBIDDEN


def test_decision_parse():
    assert Decision.parse("allow") is Decision.ALLOW
    assert Decision.parse("prompt") is Decision.PROMPT
    assert Decision.parse("forbidden") is Decision.FORBIDDEN
    with pytest.raises(ValueError):
        Decision.parse("maybe")


def test_amendment_requires_non_empty_prefix():
    with pytest.raises(ValueError):
        ExecPolicyAmendment(command=())
