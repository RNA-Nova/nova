"""Bash 执行的线上 item 变体（呈现层形状）。

与 ``BashExecutionMessage``（域真身：JSONL 落盘 + LLM 上下文）配对——
消息是事实源，本类型是其线上呈现：执行起点早建卡片（started）、输出
逐块流式（delta）、record 时由消息的 ``to_item()`` 定稿（completed）。

前端经 ``entry:bashExecution`` 槽位渲染（渲染器在包的 frontend 半区）。
"""

from __future__ import annotations

from typing import Literal, Optional

from nova_server.types.items import NovaItem


class BashExecutionItem(NovaItem):
    """用户 ``!cmd`` 执行的呈现原子（type 字符串与消息 role 同名）。"""

    type: Literal["bashExecution"] = "bashExecution"
    command: str = ""
    output: str = ""
    exit_code: Optional[int] = None
    cancelled: bool = False
    truncated: bool = False
    full_output_path: Optional[str] = None
    exclude_from_context: bool = False


__all__ = ["BashExecutionItem"]
