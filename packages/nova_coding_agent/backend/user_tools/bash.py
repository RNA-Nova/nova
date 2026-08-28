"""会话 bash 工具（``UserTool`` 约定形态）。

元数据是类属性（import 即可读，无需会话）；按会话实例化，会话上下文
经构造注入。执行语义：shell 前缀、扩展 spawn hooks
（``registerSpawnHook``）+ 单次调用注入 hook、自定义 operations
后端（远程执行等）、abort 级联由会话层贯通。执行前会话层发射
``user_bash`` 扩展事件：扩展返回完整 result 时经 ``message_from_result``
翻译为本工具消息直接记录（替换执行），返回 operations 时经
``params["operations"]`` 注入换用自定义执行后端。
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from nova_coding_agent.bash.engine import (
    BashOperations,
    compose_spawn_hooks,
    create_local_bash_operations,
)
from nova_coding_agent.bash.item import BashExecutionItem
from nova_coding_agent.bash.message import BashExecutionMessage
from nova_coding_agent.executor import (
    ExecutorBashOperations,
    get_backend_selection,
    get_executor_manager,
)

from nova_harness.core.types.extensions.process import SpawnHook
from nova_harness.core.types.resources.user_tools import UserToolEventCallback
from nova_harness.server.types.items import ItemStatus


def _result_field(result: Any, *names: str) -> Any:
    """从执行结果取字段：先 dict 键后属性，支持多候选名（snake_case / 驼峰）。

    扩展经 ``user_bash`` 事件返回的 result 形态由扩展作者决定——可能是
    ``BashResult`` 数据类、也可能是对齐 pi 驼峰键的普通 dict。
    """
    for name in names:
        if isinstance(result, dict) and name in result:
            return result[name]
        value = getattr(result, name, None)
        if value is not None:
            return value
    return None


class UserTool:
    """bash 用户工具：执行 bash 命令并将结果注入会话上下文（! 命令）。"""

    name = "bash"
    description = "执行 bash 命令并将结果注入会话上下文（! 命令）"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 bash 命令"},
            "exclude_from_context": {
                "type": "boolean",
                "description": "为 true 时结果不注入 LLM 上下文（!! 语义）",
                "default": False,
            },
        },
        "required": ["command"],
    }
    MESSAGE_TYPES = [BashExecutionMessage]

    def __init__(self, session: Any) -> None:
        self._session = session

    async def execute(
        self,
        params: Dict[str, Any],
        on_event: Optional[UserToolEventCallback],
        signal: Any,
    ) -> BashExecutionMessage:
        """执行 bash 命令并产出结果消息。

        ``on_event`` 为协议保留参数（进度不再经它透出——线上进度归 item
        发射：执行起点的 emit_item_started 早建卡片 + 输出块的
        emit_item_delta 流式，record 时归约器经 to_item() 定稿）。
        """
        session = self._session
        command = str(params.get("command", ""))

        prefix = None
        if hasattr(session.settings_manager, "get_shell_command_prefix"):
            prefix = session.settings_manager.get_shell_command_prefix()
        shell_path = None
        if hasattr(session.settings_manager, "get_shell_path"):
            shell_path = session.settings_manager.get_shell_path()
        resolved_command = f"{prefix}\n{command}" if prefix else command

        # spawn hooks：扩展注册的全局 hook + 本次调用传入的 hook
        spawn_hooks: List[SpawnHook] = []
        runner = getattr(session, "extension_runner", None)
        if runner is not None:
            spawn_hooks.extend(getattr(runner.runtime, "spawn_hooks", []) or [])
        extra_hook = params.get("spawn_hook")
        if extra_hook is not None:
            spawn_hooks.append(extra_hook)

        operations: BashOperations = params.get("operations")
        if operations is None:
            # 与 LLM bash 工具同一执行后端解析（设计定案 R3——直读 runtime 格）；
            # 显式 params["operations"] 注入仍优先（调用方自定后端）
            selection = get_backend_selection()
            if selection.backend == "executor":
                operations = ExecutorBashOperations(
                    get_executor_manager(),
                    url=selection.url,
                    remote_cwd=selection.remote_cwd,
                )
            else:
                operations = create_local_bash_operations(
                    shell_path=shell_path,
                    spawn_hook=(
                        compose_spawn_hooks(spawn_hooks) if spawn_hooks else None
                    ),
                )

        # item 早建：命令串在执行开始前上线（server 归约器承接为
        # item_started），前端流式卡片即刻渲染 `$ command` 头；record 时
        # 以 item_id 定稿同一卡片（归约器调消息的 to_item()）
        item_id = uuid.uuid4().hex[:8]
        session.emit_item_started(
            BashExecutionItem(
                id=item_id,
                status=ItemStatus.RUNNING,
                source="user",
                ts=int(time.time() * 1000),
                command=command,
                exclude_from_context=bool(params.get("exclude_from_context", False)),
            )
        )

        def on_chunk(text: str) -> None:
            """输出块流式：item delta（线上进度的唯一通道）。"""
            session.emit_item_delta(item_id, {"output": text})

        exec_signal = params.get("signal") or signal
        result = await operations.execute(
            resolved_command,
            session.session_manager.get_cwd(),
            {"on_chunk": on_chunk, "signal": exec_signal},
        )

        message = self.message_from_result(params, result)
        message.item_id = item_id
        return message

    def message_from_result(
        self, params: Dict[str, Any], result: Any
    ) -> BashExecutionMessage:
        """把执行结果翻译为 BashExecutionMessage。

        两个调用方：``execute`` 末尾（本地引擎结果）；会话层
        ``user_bash`` 拦截（扩展返回完整 result 时跳过真实执行、直接
        记录本方法构造的消息，对齐 pi ``recordBashResult``）。
        """
        return BashExecutionMessage(
            command=str(params.get("command", "")),
            output=str(_result_field(result, "output") or ""),
            exit_code=_result_field(result, "exit_code", "exitCode"),
            cancelled=bool(_result_field(result, "cancelled") or False),
            truncated=bool(_result_field(result, "truncated") or False),
            full_output_path=_result_field(
                result, "full_output_path", "fullOutputPath"
            ),
            timestamp=int(time.time() * 1000),
            exclude_from_context=bool(params.get("exclude_from_context", False)),
        )


__all__ = ["UserTool"]
