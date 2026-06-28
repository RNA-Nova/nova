"""扩展命令分发。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nova_harness.core.types.events import ExtensionErrorEvent

if TYPE_CHECKING:
    from nova_harness.core.agent_session.agent import AgentSession


class CommandDispatcher:
    """封装 AgentSession 的扩展 slash 命令执行。"""

    def __init__(self, session: "AgentSession") -> None:
        self._session = session

    async def try_execute(self, text: str) -> bool:
        """尝试执行扩展命令；返回 True 表示已处理。"""
        runner = self._session._extension_runner
        if runner is None:
            return False
        space_index = text.find(" ")
        command_name = text[1:space_index] if space_index != -1 else text[1:]
        command = runner.get_command(command_name)
        if command is None:
            return False
        args = text[space_index + 1 :] if space_index != -1 else ""
        ctx = runner.create_command_context()
        try:
            await command.handler(args, ctx)
        except Exception as err:
            runner.emit_error(
                ExtensionErrorEvent(
                    extension_path=f"command:{command_name}",
                    event="command",
                    error=str(err),
                )
            )
        return True

    def throw_if_extension_command(self, text: str) -> None:
        """如果文本是扩展命令则抛出异常（用于 steer/followUp 禁止排队）。"""
        runner = self._session._extension_runner
        if runner is None:
            return
        space_index = text.find(" ")
        command_name = text[1:space_index] if space_index != -1 else text[1:]
        if runner.get_command(command_name) is not None:
            raise RuntimeError(
                f'Extension command "/{command_name}" cannot be queued. '
                "Use prompt() or execute the command when not streaming."
            )
