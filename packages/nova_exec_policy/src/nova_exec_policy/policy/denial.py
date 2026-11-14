"""沙箱拒绝启发式（对位 codex sandboxing `denial.rs`）——"无确定性判别法"，
按退出码快排 + 关键词 + seccomp SIGSYS 特例。"""

from __future__ import annotations

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
