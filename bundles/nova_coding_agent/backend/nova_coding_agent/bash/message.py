"""Bash 执行消息类型。

会话 bash（``user_tools/bash``）执行结果的记录形态：双写 agent
state + 会话 JSONL，经 ``ContextInjectable`` 协议翻译注入 LLM 上下文。
本类随包分发——包加载时经 ``MESSAGE_TYPES`` 注册进 harness 的消息
回载注册表；包缺席时旧会话中的本类消息降级为不透明消息。
"""

from __future__ import annotations

from typing import Literal, Optional

from nova_agent import CustomAgentMessage


class BashExecutionMessage(CustomAgentMessage):
    """Message type for bash executions via the ! command."""

    command: str
    output: str
    exit_code: Optional[int]
    cancelled: bool
    truncated: bool
    full_output_path: Optional[str] = None
    timestamp: int
    exclude_from_context: bool = False
    role: Literal["bashExecution"] = "bashExecution"

    def to_context_text(self) -> str:
        """翻译为注入 LLM 上下文的 user 文本。"""
        text = f"Ran `{self.command}`\n"
        if self.output:
            text += f"```\n{self.output}\n```"
        else:
            text += "(no output)"

        if self.cancelled:
            text += "\n\n(command cancelled)"
        elif self.exit_code is not None and self.exit_code != 0:
            text += f"\n\nCommand exited with code {self.exit_code}"

        if self.truncated and self.full_output_path:
            text += f"\n\n[Output truncated. Full output: {self.full_output_path}]"

        return text


__all__ = ["BashExecutionMessage"]
