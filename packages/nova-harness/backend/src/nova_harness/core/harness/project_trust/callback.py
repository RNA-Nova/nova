"""``resolve_project_trust`` 回调工厂（print / RPC 两种入口共享）。

服务创建期（``AgentSessionServices``）只在拿到回调时才做信任决议——
此前只有 print 模式接线，RPC/TUI 启动永远默认不信任且不读
trust.json（"信任过下次还问" 的根因）。本工厂把同一条决议链
（``resolve_project_trusted``：trust.json 记录 → ``default_project_trust``
设置 →（有 UI）启动询问）封装为两种入口共享的回调构造。
"""

from __future__ import annotations

from typing import Any, Optional

from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.types.project_trust import (
    ProjectTrustContext,
    ResolveProjectTrustedOptions,
)

from .project_trust import resolve_project_trusted
from .trust_store import ProjectTrustStore


def make_resolve_project_trust_callback(
    cwd: str,
    agent_dir: str,
    ui: Any,
    has_ui: bool,
    trust_override: Optional[bool] = None,
):
    """构造服务创建期的 project trust 决议回调。

    - print 模式：``ui=NoOpUIContext()``、``has_ui=False``（无 UI 默认不信任）；
    - RPC/TUI：``ui=<RoutingUIContext>``、``has_ui=True``（未知项目弹启动
      信任框，可记忆决策）。
    """

    # default_project_trust 设置经独立 SettingsManager 读取（此时主
    # settings_manager 尚未创建——先按信任读全局配置，不触发项目加载）。
    settings_manager = SettingsManager.create(
        cwd=cwd, agent_dir=agent_dir, project_trusted=True
    )
    default_project_trust = settings_manager.get_default_project_trust()

    async def resolve_project_trust(extensions_result: Any) -> bool:
        trust_store = ProjectTrustStore.for_agent_dir(agent_dir)
        return await resolve_project_trusted(
            ResolveProjectTrustedOptions(
                cwd=cwd,
                trust_store=trust_store,
                trust_override=trust_override,
                default_project_trust=default_project_trust,
                extensions_result=extensions_result,
                project_trust_context=ProjectTrustContext(
                    cwd=cwd,
                    has_ui=has_ui,
                    ui=ui,
                ),
            )
        )

    return resolve_project_trust
