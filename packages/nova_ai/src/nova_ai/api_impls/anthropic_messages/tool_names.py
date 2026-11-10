"""Claude Code OAuth 的工具名双向规范化（pi anthropic-messages 对位）。"""

from typing import Optional, Sequence

from nova_protocol import Tool

_CLAUDE_CODE_TOOLS = (
    "Read",
    "Write",
    "Edit",
    "Bash",
    "Grep",
    "Glob",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "KillShell",
    "NotebookEdit",
    "Skill",
    "Task",
    "TaskOutput",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
)
_CLAUDE_CODE_LOOKUP = {name.lower(): name for name in _CLAUDE_CODE_TOOLS}


def to_claude_code_name(name: str) -> str:
    """本地工具名 → Claude Code 的规范大小写；未知名保持不变。"""
    return _CLAUDE_CODE_LOOKUP.get(name.lower(), name)


def from_claude_code_name(name: str, tools: Optional[Sequence[Tool]] = None) -> str:
    """服务端工具名 → 当前工具表里的本地注册名（大小写不敏感）。"""
    if tools:
        lowered = name.lower()
        matched = next(
            (tool.name for tool in tools if tool.name.lower() == lowered), None
        )
        if matched is not None:
            return matched
    return name


__all__ = ["from_claude_code_name", "to_claude_code_name"]
