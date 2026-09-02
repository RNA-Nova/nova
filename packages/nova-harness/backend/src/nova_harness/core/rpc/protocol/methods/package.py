"""Package manager JSON-RPC 方法。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry


def _package_manager(state: ServerState) -> "PackageManager":
    from nova_harness.core.package import PackageManager

    def _on_progress(event) -> None:
        # 转发安装/更新进度为 UI 通知；前端未声明该 capability 时安全降级。
        # 产出方统一为 ProgressEvent（NovaBaseModel），直接 model_dump。
        state.ui_context.notify("package_progress", event.dump_wire())

    if state.runtime is not None and state.runtime.session is not None:
        session = state.runtime.session
        project_trusted = (
            session.settings_manager.is_project_trusted()
            if session.settings_manager is not None
            else None
        )
        return PackageManager(
            cwd=session.cwd,
            settings_manager=session.settings_manager,
            project_trusted=project_trusted,
            on_progress=_on_progress,
        )
    return PackageManager(on_progress=_on_progress)


def register(registry: MethodRegistry, state: ServerState) -> None:
    async def pkgList(params: Dict[str, Any]) -> Dict[str, Any]:
        pm = _package_manager(state)
        local = params["local"]
        # 磁盘扫描 + 资源解析是阻塞 IO，放线程避免卡住事件循环
        views = await asyncio.to_thread(pm.list_with_resources, local=local)
        return {k: v.dump_wire() for k, v in views.items()}

    async def pkgInstall(params: Dict[str, Any]) -> Dict[str, Any]:
        pm = _package_manager(state)
        source = params["source"]
        local = params["local"]
        # pip 安装是长阻塞操作，必须放线程（否则 abort/ui 应答全部冻结）
        meta = await asyncio.to_thread(pm.install_and_persist, source, local=local)
        return meta.dump_wire()

    async def pkgUninstall(params: Dict[str, Any]) -> Dict[str, Any]:
        pm = _package_manager(state)
        name_or_source = params["name_or_source"]
        local = params["local"]
        result = await asyncio.to_thread(pm.uninstall, name_or_source, local=local)
        return {"ok": result.removed, "messages": result.messages}

    async def pkgInfo(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pm = _package_manager(state)
        name_or_source = params["name_or_source"]
        local = params["local"]
        meta = await asyncio.to_thread(pm.info, name_or_source, local=local)
        return meta.dump_wire() if meta else None

    async def pkgUpdate(params: Dict[str, Any]) -> List[Dict[str, Any]]:
        pm = _package_manager(state)
        name_or_source = params["name_or_source"]
        local = params["local"]
        metas = await pm.update(name_or_source, local=local)
        return [m.dump_wire() for m in metas]

    async def pkgCheckUpdates(params: Dict[str, Any]) -> Dict[str, Any]:
        """只读更新检查（前端启动拉取用）：离线/失败静默返回空列表。"""
        pm = _package_manager(state)
        try:
            updates = await pm.check_for_available_updates()
        except Exception:
            updates = []
        return {"updates": [u.dump_wire() for u in updates]}

    from nova_harness.core.rpc.protocol.methods import shapes as _sh

    _D = "package"
    registry.register("pkgList", pkgList, domain=_D, params_model=_sh.PkgParams)
    registry.register(
        "pkgInstall", pkgInstall, domain=_D, params_model=_sh.PkgInstallParams
    )
    registry.register(
        "pkgUninstall",
        pkgUninstall,
        domain=_D,
        params_model=_sh.PkgNameParams,
        result_model=_sh.PkgUninstallResult,
    )
    registry.register("pkgInfo", pkgInfo, domain=_D, params_model=_sh.PkgNameParams)
    registry.register(
        "pkgUpdate",
        pkgUpdate,
        domain=_D,
        params_model=_sh.PkgNameParams,
        result_model=_sh.PkgUpdateResult,
    )
    registry.register(
        "pkgCheckUpdates",
        pkgCheckUpdates,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.PkgCheckUpdatesResult,
    )
