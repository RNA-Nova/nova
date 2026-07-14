"""Slash 输入处理。

统一处理所有 ``/`` 开头的用户输入：

- 扩展命令：立即执行并消费输入
- prompt templates：展开为模板内容
- skill 命令：展开为 XML skill block
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from nova_harness.core.harness.skills import expand_skill_command
from nova_harness.core.resources.loaders.prompt_templates import (
    expand_prompt_template,
)
from nova_harness.core.types.events import ExtensionErrorEvent
from nova_harness.core.types.extensions.commands import RegisteredCommand
from nova_harness.core.types.protocols import AgentSessionProtocol

if TYPE_CHECKING:
    from nova_harness.core.extensions import ExtensionRunner


class SlashInputHandler:
    """统一处理 ``/`` 开头的用户输入。"""

    def __init__(self, session: AgentSessionProtocol) -> None:
        self._session = session

    async def handle(self, text: str) -> Optional[str]:
        """处理 slash 输入。

        Returns:
            ``None``: 输入已被消费（扩展命令已执行）。
            ``str``: 转换后的文本（prompt/skill 展开），调用方应继续走 agent 流程。
        """
        if await self.execute_command(text):
            return None
        return self.expand_skill_and_prompt(text)

    async def execute_command(self, text: str) -> bool:
        """如果 *text* 是已注册的扩展命令，立即执行并返回 ``True``；否则返回 ``False``。"""
        runner = self._session._extension_runner
        if runner is None:
            return False

        command_name = self._parse_command_name(text)
        command = runner.get_command(command_name)
        if command is None:
            return False

        await self._execute_command(command, command_name, text, runner)
        return True

    def expand_skill_and_prompt(self, text: str) -> str:
        """展开 ``/skill:name`` 与 prompt template；不是则原样返回。"""
        # 1. skill 命令展开
        expanded = expand_skill_command(text, self._session._get_allowed_skills())
        if expanded != text:
            return expanded

        # 2. prompt template 展开
        prompts = self._session.resource_loader.get_prompts().get("prompts", [])
        expanded = expand_prompt_template(text, prompts)
        if expanded != text:
            return expanded

        return text

    def is_extension_command(self, text: str) -> bool:
        """判断 ``text`` 是否是已注册的扩展命令。

        用于 ``steer()`` / ``follow_up()`` 禁止扩展命令排队。
        """
        runner = self._session._extension_runner
        if runner is None:
            return False
        command_name = self._parse_command_name(text)
        return runner.get_command(command_name) is not None

    @staticmethod
    def _parse_command_name(text: str) -> str:
        """从 ``/name args`` 中解析命令名。"""
        if not text.startswith("/"):
            return ""
        space_index = text.find(" ")
        if space_index == -1:
            return text[1:]
        return text[1:space_index]

    @staticmethod
    def _parse_args(text: str) -> str:
        """从 ``/name args`` 中解析参数。"""
        space_index = text.find(" ")
        if space_index == -1:
            return ""
        return text[space_index + 1 :]

    async def _execute_command(
        self,
        command: RegisteredCommand,
        command_name: str,
        text: str,
        runner: ExtensionRunner,
    ) -> None:
        """执行单个扩展命令并捕获异常。"""
        args = self._parse_args(text)
        ctx = runner.create_command_context()
        try:
            await command.handler(args, ctx)
        except Exception as err:
            runner.emit_error(
                ExtensionErrorEvent(
                    extension_path=command.extension_path,
                    event="command",
                    error=str(err),
                )
            )
