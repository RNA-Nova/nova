"""Bash 执行消息类型。

会话 bash（``user_tools/bash``）执行结果的记录形态：双写 agent
state + 会话 JSONL，经 ``ContextInjectable`` 协议翻译注入 LLM 上下文。
本类随包分发——包加载时经 ``MESSAGE_TYPES`` 注册进 harness 的消息
回载注册表；包缺席时旧会话中的本类消息降级为不透明消息。
"""

from __future__ import annotations

from typing import Literal, Optional

from nova_agent import CustomAgentMessage
from nova_server.types.items import ItemStatus

from nova_coding_agent.bash.item import BashExecutionItem


class BashExecutionMessage(CustomAgentMessage):
    """Message type for bash executions via the ! command."""

    command: str
    output: str
    exit_code: Optional[int]
    cancelled: bool
    truncated: bool
    full_output_path: Optional[str] = None
    # item 身份：实时流式卡片的 id——record 时 server 归约器据此把本消息
    # 定稿到已建立的在飞卡片；空串表示未经流式（扩展拦截路径），按一次性处理
    item_id: str = ""
    timestamp: int
    exclude_from_context: bool = False
    role: Literal["bashExecution"] = "bashExecution"

    def to_item(self) -> BashExecutionItem:
        """呈现桥：消息 → item 权威终态（实时定稿与恢复读共用）。

        状态语义：cancelled → cancelled；非零退出码 → failed；其余 → done
        （退出码缺失视作正常结束——执行引擎恒返回退出码，缺失只见于
        扩展拦截路径的自构结果）。
        """
        if self.cancelled:
            status = ItemStatus.CANCELLED
        elif self.exit_code is not None and self.exit_code != 0:
            status = ItemStatus.FAILED
        else:
            status = ItemStatus.DONE
        return BashExecutionItem(
            id=self.item_id,
            type="bashExecution",
            status=status,
            source="user",
            ts=self.timestamp,
            command=self.command,
            output=self.output,
            exit_code=self.exit_code,
            cancelled=self.cancelled,
            truncated=self.truncated,
            full_output_path=self.full_output_path,
            exclude_from_context=self.exclude_from_context,
        )

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
