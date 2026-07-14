"""会话 cwd 校验工具。

在恢复/导入会话时检查会话头部保存的工作目录是否仍然存在。
"""

from __future__ import annotations

import os
from typing import Optional, Protocol

from nova_harness.core.types.session.issues import SessionCwdIssue


def format_missing_session_cwd_error(issue: SessionCwdIssue) -> str:
    """格式化 MissingSessionCwdError 的错误文本。"""
    session_file = f"\nSession file: {issue.session_file}" if issue.session_file else ""
    return (
        f"Stored session working directory does not exist: {issue.session_cwd}"
        f"{session_file}\n"
        f"Current working directory: {issue.fallback_cwd}"
    )


def format_missing_session_cwd_prompt(issue: SessionCwdIssue) -> str:
    """格式化用于询问用户是否继续的提示文本。"""
    session_file = f"\nSession file: {issue.session_file}" if issue.session_file else ""
    return (
        f"The stored session working directory no longer exists: {issue.session_cwd}"
        f"{session_file}\n"
        f"Current working directory: {issue.fallback_cwd}\n"
        "Do you want to continue with the current directory?"
    )


class _SessionCwdSource(Protocol):
    """用于 cwd 校验的最小协议。"""

    def get_cwd(self) -> str: ...
    def get_session_file(self) -> Optional[str]: ...


def get_missing_session_cwd_issue(
    session_manager: _SessionCwdSource,
    fallback_cwd: str,
) -> Optional[SessionCwdIssue]:
    """检查会话保存的 cwd 是否缺失。

    仅当存在 session file 且 cwd 不存在时返回 ``SessionCwdIssue``；
    否则返回 ``None``。
    """
    session_file = session_manager.get_session_file()
    if not session_file:
        return None

    session_cwd = session_manager.get_cwd()
    if not session_cwd or os.path.exists(session_cwd):
        return None
    return SessionCwdIssue(
        session_file=session_file,
        session_cwd=session_cwd,
        fallback_cwd=fallback_cwd,
    )


class MissingSessionCwdError(FileNotFoundError):
    """会话保存的工作目录不存在时抛出。"""

    def __init__(self, issue: SessionCwdIssue) -> None:
        super().__init__(format_missing_session_cwd_error(issue))
        self.issue = issue


def assert_session_cwd_exists(
    session_manager: _SessionCwdSource,
    fallback_cwd: str,
) -> None:
    """断言会话保存的 cwd 存在，不存在时抛出 ``MissingSessionCwdError``。"""
    issue = get_missing_session_cwd_issue(session_manager, fallback_cwd)
    if issue is not None:
        raise MissingSessionCwdError(issue)


__all__ = [
    "MissingSessionCwdError",
    "SessionCwdIssue",
    "assert_session_cwd_exists",
    "format_missing_session_cwd_error",
    "format_missing_session_cwd_prompt",
    "get_missing_session_cwd_issue",
]
