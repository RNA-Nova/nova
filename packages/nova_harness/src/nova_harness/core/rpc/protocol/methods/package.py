"""Package manager JSON-RPC 方法。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from nova_harness.core.rpc.protocol.methods import shapes as _sh
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry

_D = "package"


def _package_manager(state: ServerState) -> "PackageManager":
    from nova_harness.core.package import PackageManager

    def _on_progress(event) -> None:
        # 转发安装/更新进度为 UI 通知；前端未声明该 capability 时安全降级。
        # 产出方统一为 ProgressEvent（NovaBaseModel），线上形态 dump_wire。
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
    async def pkgList(params: _sh.PkgParams) -> Dict[str, Any]:
        pm = _package_manager(state)
        # 磁盘扫描 + 资源解析是阻塞 IO，放线程避免卡住事件循环
        views = await asyncio.to_thread(pm.list_with_resources, local=params.local)
        return {k: v.dump_wire() for k, v in views.items()}

    async def pkgInstall(params: _sh.PkgInstallParams) -> Dict[str, Any]:
        pm = _package_manager(state)
        # pip 安装是长阻塞操作，必须放线程（否则 abort/ui 应答全部冻结）
        meta = await asyncio.to_thread(
            pm.install_and_persist, params.source, local=params.local
        )
        return meta.dump_wire()

    async def pkgUninstall(params: _sh.PkgNameParams) -> _sh.PkgUninstallResult:
        pm = _package_manager(state)
        result = await asyncio.to_thread(
            pm.uninstall, params.name_or_source, local=params.local
        )
        return _sh.PkgUninstallResult(ok=result.removed, messages=result.messages)

    async def pkgInfo(params: _sh.PkgNameParams) -> Optional[Dict[str, Any]]:
        pm = _package_manager(state)
        meta = await asyncio.to_thread(
            pm.info, params.name_or_source, local=params.local
        )
        return meta.dump_wire() if meta else None

    async def pkgUpdate(params: _sh.PkgNameParams) -> _sh.PkgUpdateResult:
        pm = _package_manager(state)
        metas = await pm.update(params.name_or_source, local=params.local)
        return _sh.PkgUpdateResult(root=[m.dump_wire() for m in metas])

    async def pkgCheckUpdates(params: _sh.EmptyParams) -> _sh.PkgCheckUpdatesResult:
        """只读更新检查（前端启动拉取用）：离线/失败静默返回空列表。"""
        pm = _package_manager(state)
        try:
            updates = await pm.check_for_available_updates()
        except Exception:
            updates = []
        return _sh.PkgCheckUpdatesResult(
            updates=[
                _sh.PackageUpdateItem(
                    source=u.source,
                    display_name=u.display_name,
                    type=u.type,
                    scope=u.scope,
                )
                for u in updates
            ]
        )

    registry.register("pkgList", pkgList, domain=_D)
    registry.register("pkgInstall", pkgInstall, domain=_D)
    registry.register("pkgUninstall", pkgUninstall, domain=_D)
    registry.register("pkgInfo", pkgInfo, domain=_D)
    registry.register("pkgUpdate", pkgUpdate, domain=_D)
    registry.register("pkgCheckUpdates", pkgCheckUpdates, domain=_D)
