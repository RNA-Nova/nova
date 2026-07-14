"""会话生命周期中的问题描述类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SessionCwdIssue:
    """缺失会话 cwd 的问题描述。"""

    session_file: Optional[str]
    session_cwd: str
    fallback_cwd: str


__all__ = ["SessionCwdIssue"]
