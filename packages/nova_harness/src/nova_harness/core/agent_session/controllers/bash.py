"""Bash 执行控制。"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from nova_harness.core.types.messages import BashExecutionMessage
from nova_harness.core.utils.bash import (
    BashOperations,
    BashResult,
    create_local_bash_operations,
    execute_bash,
)

if TYPE_CHECKING:
    from nova_harness.core.agent_session.agent import AgentSession


class BashController:
    """封装 AgentSession 的 bash 命令执行与结果记录。"""

    def __init__(self, session: "AgentSession") -> None:
        self._session = session

    @property
    def is_running(self) -> bool:
        return self._session._bash_abort_event is not None

    @property
    def has_pending_messages(self) -> bool:
        return len(self._session._pending_bash_messages) > 0

    async def execute_bash(
        self,
        command: str,
        on_chunk: Optional[Callable[[str], None]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> BashResult:
        """执行一条 bash 命令并记录结果到会话。"""
        opts = options or {}
        prefix = None
        if hasattr(self._session.settings_manager, "get_shell_command_prefix"):
            prefix = self._session.settings_manager.get_shell_command_prefix()
        shell_path = None
        if hasattr(self._session.settings_manager, "get_shell_path"):
            shell_path = self._session.settings_manager.get_shell_path()

        resolved_command = f"{prefix}\n{command}" if prefix else command

        self._session._bash_abort_event = asyncio.Event()
        signal = opts.get("signal") or self._session._bash_abort_event
        operations: BashOperations = opts.get(
            "operations", create_local_bash_operations(shell_path=shell_path)
        )

        try:
            result = await execute_bash(
                resolved_command,
                self._session.session_manager.get_cwd(),
                operations,
                {"on_chunk": on_chunk, "signal": signal},
            )
        finally:
            self._session._bash_abort_event = None

        self.record_bash_result(command, result, opts)
        return result

    def record_bash_result(
        self,
        command: str,
        result: BashResult,
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """将 bash 执行结果记录为 BashExecutionMessage。"""
        opts = options or {}
        bash_message = BashExecutionMessage(
            command=command,
            output=result.output,
            exit_code=result.exit_code,
            cancelled=result.cancelled,
            truncated=result.truncated,
            full_output_path=result.full_output_path,
            timestamp=int(time.time() * 1000),
            exclude_from_context=opts.get("exclude_from_context", False),
        )
        if self._session.is_streaming:
            self._session._pending_bash_messages.append(bash_message)
        else:
            self._session.agent.state.messages.append(bash_message)
            self._session.session_manager.append_message(bash_message)

    def abort_bash(self) -> None:
        """取消正在运行的 bash 命令。"""
        if self._session._bash_abort_event is not None:
            self._session._bash_abort_event.set()

    def flush_pending(self) -> None:
        """把运行期间累积的 bash 消息追加到会话。"""
        if not self._session._pending_bash_messages:
            return
        for message in self._session._pending_bash_messages:
            self._session.agent.state.messages.append(message)
            self._session.session_manager.append_message(message)
        self._session._pending_bash_messages = []
