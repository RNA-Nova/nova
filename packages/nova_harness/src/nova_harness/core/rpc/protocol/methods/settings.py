"""Settings 域 JSON-RPC 方法。

前端读写用户设置的通道（远程前端无法触达后端本地 settings 文件）：
- ``getSettings``：合并生效配置（global + project 深合并，None 字段省略）；
- ``updateSettings``：全局层部分更新（``Settings`` 模型校验，未知键/类型错误
  直接拒绝），持久化走 SettingsManager 写队列。
"""

from __future__ import annotations

from typing import Any, Dict

from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry


def register(registry: MethodRegistry, state: ServerState) -> None:
    def _settings_manager(params: Dict[str, Any]) -> Any:
        """settings 是 agent_dir/cwd 绑定而非会话绑定：无会话时也能读写。"""
        if state.runtime is not None:
            return state.runtime.services.settings_manager
        if state.fallback_settings_manager is None:
            import os

            from nova_harness.core.config.settings.manager import SettingsManager

            state.fallback_settings_manager = SettingsManager.create(
                cwd=params.get("cwd") or os.getcwd()
            )
        return state.fallback_settings_manager

    def _dump_effective(manager: Any) -> Dict[str, Any]:
        settings = manager.get_settings()
        return {k: v for k, v in settings.dump_wire().items() if v is not None}

    async def getSettings(params: Dict[str, Any]) -> Dict[str, Any]:
        """合并生效配置（None 字段省略，前端按各自默认值兜底）。"""
        return {"settings": _dump_effective(_settings_manager(params))}

    async def updateSettings(params: Dict[str, Any]) -> Dict[str, Any]:
        """全局层部分更新：``{"settings": {...}}``，返回更新后的生效配置。

        资源管控类键（tools/user_tools/personas/role_boundary 等）变更后
        自动重解析（会话 reload——注册表/系统提示词/资源全量重建）；
        展示偏好类键（主题/编辑器 padding 等）不触发。
        """
        manager = _settings_manager(params)
        partial = params["settings"]
        try:
            manager.update_global_settings(partial)
        except ValueError as exc:
            raise JSONRPCError(JSONRPCError.INVALID_PARAMS, str(exc)) from exc
        except Exception as exc:  # pydantic ValidationError 等
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS, f"Invalid settings: {exc}"
            ) from exc
        # 资源管控键 → 会话重解析（入参键先归一化为 snake 再比对——
        # camel/snake 双形态由归一化兜底，不维护双份清单）
        from pydantic.alias_generators import to_snake

        resource_keys = {
            "tools",
            "user_tools",
            "personas",
            "role_boundary",
            "extensions",
            "skills",
            "prompts",
            "agents",
        }
        normalized = {to_snake(key) for key in partial}
        if normalized & resource_keys and state.runtime is not None:
            await state.runtime.session.reload()
        return {"ok": True, "settings": _dump_effective(manager)}

    # ------------------------------------------------------------------
    # 资源管控意图级方法（名字级：tools / user_tools）
    #
    # 增量语义：只增/删 ``!name`` 单条 pattern，不整键覆盖；写入后触发
    # 会话级重解析（注册表重建 + 系统提示词重算），并返回生效 patterns。
    # ------------------------------------------------------------------

    _PATTERN_ACCESSORS = {
        "tools": ("get_tool_patterns", "set_tool_patterns"),
        "user_tools": ("get_user_tool_patterns", "set_user_tool_patterns"),
    }

    def _accessors(resource_type: str) -> Any:
        accessors = _PATTERN_ACCESSORS.get(resource_type)
        if accessors is None:
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS,
                f"Unsupported resourceType: {resource_type}"
                f" (expected one of {sorted(_PATTERN_ACCESSORS)})",
            )
        return accessors

    async def _re_resolve(resource_type: str) -> None:
        """settings 变更后的会话级重解析（有会话时才需要）。"""
        if state.runtime is None:
            return
        session = getattr(state.runtime, "session", None)
        if session is None:
            return
        if resource_type == "tools":
            session._tools.refresh_registry()
            session._sync_system_prompt()
        elif resource_type == "user_tools":
            session._refresh_user_tools()

    async def excludeResource(params: Dict[str, Any]) -> Dict[str, Any]:
        """长期禁用某资源（settings 追加 ``!name``，幂等）。"""
        get_name, set_name = _accessors(params["resource_type"])
        manager = _settings_manager(params)
        patterns = list(getattr(manager, get_name)())
        entry = f"!{params['name']}"
        if entry not in patterns:
            patterns.append(entry)
            getattr(manager, set_name)(patterns)
        await _re_resolve(params["resource_type"])
        return {"ok": True, "patterns": getattr(manager, get_name)()}

    async def includeResource(params: Dict[str, Any]) -> Dict[str, Any]:
        """撤销长期禁用（移除 ``!name`` 条目，幂等）。"""
        get_name, set_name = _accessors(params["resource_type"])
        manager = _settings_manager(params)
        entry = f"!{params['name']}"
        patterns = [p for p in getattr(manager, get_name)() if p != entry]
        getattr(manager, set_name)(patterns)
        await _re_resolve(params["resource_type"])
        return {"ok": True, "patterns": getattr(manager, get_name)()}

    from nova_harness.core.rpc.protocol.methods import shapes as _sh

    _D = "settings"
    registry.register(
        "getSettings",
        getSettings,
        domain=_D,
        params_model=_sh.GetSettingsParams,
        result_model=_sh.GetSettingsResult,
    )
    registry.register(
        "updateSettings",
        updateSettings,
        domain=_D,
        params_model=_sh.UpdateSettingsParams,
        result_model=_sh.UpdateSettingsResult,
    )
    registry.register(
        "excludeResource",
        excludeResource,
        domain=_D,
        params_model=_sh.SetResourceExclusionParams,
        result_model=_sh.SetResourceExclusionResult,
    )
    registry.register(
        "includeResource",
        includeResource,
        domain=_D,
        params_model=_sh.SetResourceExclusionParams,
        result_model=_sh.SetResourceExclusionResult,
    )
