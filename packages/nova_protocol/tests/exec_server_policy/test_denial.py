"""沙箱拒绝启发式的公开判定测试。"""

from nova_protocol.exec_server_policy import (
    is_likely_executor_managed_sandbox_denied,
    is_likely_sandbox_denied,
)


def test_executor_managed_denied_keywords():
    assert (
        is_likely_executor_managed_sandbox_denied(1, "", "Operation not permitted")
        is True
    )
    assert is_likely_executor_managed_sandbox_denied(1, "", "file not found") is False
    assert (
        is_likely_executor_managed_sandbox_denied(0, "", "Operation not permitted")
        is False
    )


def test_sandbox_denied_heuristic():
    # 未沙箱 → 否
    assert is_likely_sandbox_denied(None, 1, "", "Operation not permitted") is False
    # 零退出码 → 否
    assert is_likely_sandbox_denied("linux_seccomp", 0, "", "x") is False
    # 快速拒绝码 → 否（即便含关键词也先被关键词判真——关键词优先）
    assert is_likely_sandbox_denied("seatbelt", 126, "", "Permission denied") is True
    assert (
        is_likely_sandbox_denied("seatbelt", 126, "", "zsh: command not found") is False
    )
    # seccomp SIGSYS → 真
    assert is_likely_sandbox_denied("linux_seccomp", 159, "", "") is True
    assert is_likely_sandbox_denied("seatbelt", 159, "", "") is False
