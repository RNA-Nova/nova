"""Exec 模式：非交互式命令行运行。

Exec 模式是 Nova Harness 在没有 RPC/WebSocket 前端时的降级运行方式：

- ``text`` 输出：只打印最终 assistant 回复文本。
- ``json`` 输出：把 Agent 事件以 JSONL 流输出到 stdout。

本模块不依赖 UI 能力，使用 ``NoOpUIContext``。
"""

from nova_server.exec.runner import ExecRunner, run_exec_mode

__all__ = [
    "ExecRunner",
    "run_exec_mode",
]
