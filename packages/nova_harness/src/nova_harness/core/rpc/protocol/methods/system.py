"""System 相关 JSON-RPC 方法。"""

from __future__ import annotations

from nova_harness.core.rpc.connection import current_connection
from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.methods import shapes as _sh
from nova_harness.core.rpc.protocol.methods.resources import serialize_source_info
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry

_D = "system"


def register(registry: MethodRegistry, state: ServerState) -> None:
    async def getCommands(params: _sh.EmptyParams) -> _sh.GetCommandsResult:
        if state.runtime is None:
            return _sh.GetCommandsResult(commands=[])

        commands = []

        # 扩展命令（invocation_name 未重命名时为 None——线上必须给可调用名）
        for command in state.runtime.session.extension_runner.get_registered_commands():
            commands.append(
                _sh.CommandInfo(
                    name=command.resolved_name,
                    description=command.description,
                    source="extension",
                    source_info=serialize_source_info(command.source_info),
                )
            )

        # prompt templates
        for template in state.runtime.session.prompt_templates:
            commands.append(
                _sh.CommandInfo(
                    name=template.name,
                    description=template.description,
                    source="prompt",
                    source_info=serialize_source_info(template.source_info),
                )
            )

        # skills
        for skill in (
            state.runtime.session.resource_loader.get_skills()
            .get("skills", {})
            .values()
        ):
            commands.append(
                _sh.CommandInfo(
                    name=f"skill:{skill.name}",
                    description=skill.description,
                    source="skill",
                    source_info=serialize_source_info(skill.source_info),
                )
            )

        return _sh.GetCommandsResult(commands=commands)

    async def getShortcuts(params: _sh.EmptyParams) -> _sh.GetShortcutsResult:
        """扩展注册的快捷键目录（键名/描述/来源）。

        键位捕获、内置键位表、冲突裁决与用户自定义归前端；运行时只持有
        注册表与 handler（执行体）。冲突诊断随扩展诊断事件透出。
        """
        if state.runtime is None:
            return _sh.GetShortcutsResult(shortcuts=[])
        runner = state.runtime.session.extension_runner
        if runner is None:
            return _sh.GetShortcutsResult(shortcuts=[])
        return _sh.GetShortcutsResult(
            shortcuts=[
                _sh.ShortcutInfo(
                    shortcut=shortcut.shortcut,
                    description=getattr(shortcut, "description", None),
                    extension_path=getattr(shortcut, "extension_path", None),
                )
                for shortcut in runner.get_shortcuts().values()
            ],
        )

    async def invokeShortcut(params: _sh.InvokeShortcutParams) -> _sh.OkResult:
        """前端键位捕获后的回调：分发到对应扩展 handler（异步执行）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        runner = state.runtime.session.extension_runner
        if runner is None:
            return _sh.OkResult(ok=False)
        handled = await runner.invoke_shortcut(params.shortcut)
        return _sh.OkResult(ok=handled)

    async def getExtensionFlags(
        params: _sh.EmptyParams,
    ) -> _sh.GetExtensionFlagsResult:
        """扩展注册的 CLI flags（定义 + 当前值）。"""
        if state.runtime is None:
            return _sh.GetExtensionFlagsResult(flags=[])
        runner = state.runtime.session.extension_runner
        if runner is None:
            return _sh.GetExtensionFlagsResult(flags=[])
        values = runner.get_flag_values()
        flags = []
        for name, flag in runner.get_flags().items():
            flags.append(
                _sh.ExtensionFlagInfo(
                    name=name,
                    description=getattr(flag, "description", None),
                    type=getattr(flag, "type", None),
                    default=getattr(flag, "default", None),
                    value=values.get(name, getattr(flag, "default", None)),
                    extension_path=getattr(flag, "extension_path", None),
                )
            )
        return _sh.GetExtensionFlagsResult(flags=flags)

    async def setExtensionFlag(
        params: _sh.SetExtensionFlagParams,
    ) -> _sh.SetExtensionFlagResult:
        """设置扩展 flag 的值（仅限已注册的 flag 名）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        runner = state.runtime.session.extension_runner
        if runner is None:
            raise JSONRPCError(JSONRPCError.INVALID_PARAMS, "No extensions loaded")
        flags = runner.get_flags()
        if params.name not in flags:
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS, f"Unknown extension flag: '{params.name}'"
            )
        runner.set_flag_value(params.name, params.value)
        return _sh.SetExtensionFlagResult(ok=True, name=params.name, value=params.value)

    async def cancelRequest(params: _sh.CancelRequestParams) -> _sh.CancelRequestResult:
        """按 RPC request id 取消正在执行的调用（LSP $/cancelRequest 的方法版）。

        取消语义：task.cancel() → CancelledError 沿被调用的 await 链穿透
        （如 OAuth 轮询的 sleep），server 写回 -32800 应答。幂等：id 不存在
        或调用已完成 → cancelled=False（不视为错误）。

        连接隔离（连接化重构）：只查**本连接**的在飞请求表——客户端只能
        取消自己的调用，两个客户端用相同 id 也不会误伤对方。
        """
        conn = current_connection()
        task = conn.request_tasks.get(params.id) if conn is not None else None
        if task is None or task.done():
            return _sh.CancelRequestResult(ok=True, cancelled=False)
        task.cancel()
        return _sh.CancelRequestResult(ok=True, cancelled=True)

    registry.register("getCommands", getCommands, domain=_D)
    registry.register("getShortcuts", getShortcuts, domain=_D)
    registry.register("invokeShortcut", invokeShortcut, domain=_D)
    registry.register("getExtensionFlags", getExtensionFlags, domain=_D)
    registry.register("setExtensionFlag", setExtensionFlag, domain=_D)
    registry.register("cancelRequest", cancelRequest, domain=_D)
