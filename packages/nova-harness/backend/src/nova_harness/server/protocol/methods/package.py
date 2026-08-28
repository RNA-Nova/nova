"""Package manager JSON-RPC 方法。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from nova_harness.server.protocol.errors import JSONRPCError
from nova_harness.server.protocol.methods import shapes
from nova_harness.server.protocol.methods.shapes import PkgUninstallResult
from nova_harness.server.protocol.methods.state import ServerState
from nova_harness.server.protocol.router import MethodRegistry


def _package_manager(state: ServerState) -> "PackageManager":
    from nova_harness.package import PackageManager

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
    async def pkgList(params: shapes.PkgParams) -> Dict[str, Any]:
        pm = _package_manager(state)
        local = params.local
        # 磁盘扫描 + 资源解析是阻塞 IO，放线程避免卡住事件循环
        views = await asyncio.to_thread(pm.list_with_resources, local=local)
        return {k: v for k, v in views.items()}

    async def pkgInstall(params: shapes.PkgInstallParams) -> Dict[str, Any]:
        pm = _package_manager(state)
        source = params.source
        local = params.local
        # pip 安装是长阻塞操作，必须放线程（否则 abort/ui 应答全部冻结）
        meta = await asyncio.to_thread(pm.install_and_persist, source, local=local)
        return meta

    async def pkgUninstall(params: shapes.PkgNameParams) -> PkgUninstallResult:
        pm = _package_manager(state)
        name_or_source = params.name_or_source
        local = params.local
        result = await asyncio.to_thread(pm.uninstall, name_or_source, local=local)
        return PkgUninstallResult(success=result.removed, messages=result.messages)

    async def pkgInfo(params: shapes.PkgNameParams) -> Optional[Dict[str, Any]]:
        pm = _package_manager(state)
        name_or_source = params.name_or_source
        local = params.local
        meta = await asyncio.to_thread(pm.info, name_or_source, local=local)
        return meta

    async def pkgUpdate(params: shapes.PkgNameParams) -> shapes.PkgUpdateResult:
        pm = _package_manager(state)
        name_or_source = params.name_or_source
        local = params.local
        metas = await pm.update(name_or_source, local=local)
        return shapes.PkgUpdateResult(root=[m.dump_wire() for m in metas])

    async def pkgCheckUpdates(
        params: shapes.EmptyParams,
    ) -> shapes.PkgCheckUpdatesResult:
        """只读更新检查（前端启动拉取用）：离线/失败静默返回空列表。"""
        pm = _package_manager(state)
        try:
            updates = await pm.check_for_available_updates()
        except Exception:
            updates = []
        # PackageUpdate（core 类型）与契约 PackageUpdateItem 不同构，dump 后重建
        return shapes.PkgCheckUpdatesResult(updates=[u.model_dump() for u in updates])

    _D = "package"
    registry.register("pkgList", pkgList, domain=_D)
    registry.register(
        "pkgInstall", pkgInstall, domain=_D
    )
    registry.register(
        "pkgUninstall",
        pkgUninstall,
        domain=_D,
    )
    registry.register("pkgInfo", pkgInfo, domain=_D)
    registry.register(
        "pkgUpdate",
        pkgUpdate,
        domain=_D,
    )
    registry.register(
        "pkgCheckUpdates",
        pkgCheckUpdates,
        domain=_D,
    )
