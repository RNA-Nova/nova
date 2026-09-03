"""System 相关 JSON-RPC 方法。"""

from __future__ import annotations

from typing import Any, Dict, List

from nova_harness.core.rpc.connection import current_connection
from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.methods.resources import serialize_source_info
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry


def register(registry: MethodRegistry, state: ServerState) -> None:
    async def getCommands(params: Dict[str, Any]) -> Dict[str, Any]:
        if state.runtime is None:
            return {"commands": []}

        commands: List[Dict[str, Any]] = []

        # 扩展命令（invocation_name 未重命名时为 None——线上必须给可调用名）
        for command in state.runtime.session.extension_runner.get_registered_commands():
            commands.append(
                {
                    "name": command.resolved_name,
                    "description": command.description,
                    "source": "extension",
                    "source_info": serialize_source_info(command.source_info),
                }
            )

        # prompt templates
        for template in state.runtime.session.prompt_templates:
            commands.append(
                {
                    "name": template.name,
                    "description": template.description,
                    "source": "prompt",
                    "source_info": serialize_source_info(template.source_info),
                }
            )

        # skills
        for skill in (
            state.runtime.session.resource_loader.get_skills()
            .get("skills", {})
            .values()
        ):
            commands.append(
                {
                    "name": f"skill:{skill.name}",
                    "description": skill.description,
                    "source": "skill",
                    "source_info": serialize_source_info(skill.source_info),
                }
            )

        return {"commands": commands}

    async def getShortcuts(params: Dict[str, Any]) -> Dict[str, Any]:
        """扩展注册的快捷键目录（键名/描述/来源）。

        键位捕获、内置键位表、冲突裁决与用户自定义归前端；运行时只持有
        注册表与 handler（执行体）。冲突诊断随扩展诊断事件透出。
        """
        if state.runtime is None:
            return {"shortcuts": []}
        runner = state.runtime.session.extension_runner
        if runner is None:
            return {"shortcuts": []}
        return {
            "shortcuts": [
                {
                    "shortcut": shortcut.shortcut,
                    "description": getattr(shortcut, "description", None),
                    "extension_path": getattr(shortcut, "extension_path", None),
                }
                for shortcut in runner.get_shortcuts().values()
            ],
        }

    async def invokeShortcut(params: Dict[str, Any]) -> Dict[str, Any]:
        """前端键位捕获后的回调：分发到对应扩展 handler（异步执行）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        key = params["shortcut"]
        runner = state.runtime.session.extension_runner
        if runner is None:
            return {"ok": False, "reason": "no extensions loaded"}
        handled = await runner.invoke_shortcut(key)
        return {"ok": handled}

    async def getExtensionFlags(params: Dict[str, Any]) -> Dict[str, Any]:
        """扩展注册的 CLI flags（定义 + 当前值）。"""
        if state.runtime is None:
            return {"flags": []}
        runner = state.runtime.session.extension_runner
        if runner is None:
            return {"flags": []}
        values = runner.get_flag_values()
        flags: List[Dict[str, Any]] = []
        for name, flag in runner.get_flags().items():
            flags.append(
                {
                    "name": name,
                    "description": getattr(flag, "description", None),
                    "type": getattr(flag, "type", None),
                    "default": getattr(flag, "default", None),
                    "value": values.get(name, getattr(flag, "default", None)),
                    "extension_path": getattr(flag, "extension_path", None),
                }
            )
        return {"flags": flags}

    async def setExtensionFlag(params: Dict[str, Any]) -> Dict[str, Any]:
        """设置扩展 flag 的值（仅限已注册的 flag 名）。"""
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        runner = state.runtime.session.extension_runner
        if runner is None:
            raise JSONRPCError(JSONRPCError.INVALID_PARAMS, "No extensions loaded")
        name = params["name"]
        flags = runner.get_flags()
        if name not in flags:
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS, f"Unknown extension flag: '{name}'"
            )
        value = params.get("value", flags[name].default)
        runner.set_flag_value(name, value)
        return {"ok": True, "name": name, "value": value}

    async def cancelRequest(params: Dict[str, Any]) -> Dict[str, Any]:
        """按 RPC request id 取消正在执行的调用（LSP $/cancelRequest 的方法版）。

        取消语义：task.cancel() → CancelledError 沿被调用的 await 链穿透
        （如 OAuth 轮询的 sleep），server 写回 -32800 应答。幂等：id 不存在
        或调用已完成 → cancelled=False（不视为错误）。

        连接隔离（连接化重构）：只查**本连接**的在飞请求表——客户端只能
        取消自己的调用，两个客户端用相同 id 也不会误伤对方。
        """
        conn = current_connection()
        task = conn.request_tasks.get(params["id"]) if conn is not None else None
        if task is None or task.done():
            return {"ok": True, "cancelled": False}
        task.cancel()
        return {"ok": True, "cancelled": True}

    from nova_harness.core.rpc.protocol.methods import shapes as _sh

    _D = "system"
    registry.register(
        "getCommands",
        getCommands,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.GetCommandsResult,
    )
    registry.register(
        "getShortcuts",
        getShortcuts,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.GetShortcutsResult,
    )
    registry.register(
        "invokeShortcut",
        invokeShortcut,
        domain=_D,
        params_model=_sh.InvokeShortcutParams,
        result_model=_sh.OkResult,
    )
    registry.register(
        "getExtensionFlags",
        getExtensionFlags,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.GetExtensionFlagsResult,
    )
    registry.register(
        "setExtensionFlag",
        setExtensionFlag,
        domain=_D,
        params_model=_sh.SetExtensionFlagParams,
        result_model=_sh.SetExtensionFlagResult,
    )
    registry.register(
        "cancelRequest",
        cancelRequest,
        domain=_D,
        params_model=_sh.CancelRequestParams,
        result_model=_sh.CancelRequestResult,
    )
