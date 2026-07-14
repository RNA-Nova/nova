"""
Nova Harness SDK — 创建和管理 AgentSession 的高层工厂函数。

- ``create_agent_session`` 返回 ``CreateAgentSessionResult``（session + extensions_result + model_fallback_message）。
- ``create_agent_session_runtime`` 在此基础上包装为 ``AgentSessionRuntime``，供 CLI/RPC 使用。
- ``create_agent_session_services`` / ``create_agent_session_from_services`` 拆分服务创建与会话创建。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

from nova_harness.core.agent_session import (
    AgentSession,
    AgentSessionRuntime,
    AgentSessionServices,
)
from nova_harness.core.agent_session.factory import (
    build_agent_session_config,
    configure_extension_runner,
    create_agent,
    resolve_initial_active_tool_names,
    resolve_session_manager,
    restore_or_persist_session_state,
)
from nova_harness.core.config.defaults import (
    AGENTS_DIR_NAME,
    get_agent_dir,
)
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.model_resolver import (
    find_initial_model,
    resolve_thinking_level,
    restore_model_from_session,
)
from nova_harness.core.package import PackageManager
from nova_harness.core.package.metadata.validation import is_agent_dir
from nova_harness.core.types.session.config import CreateAgentSessionOptions
from nova_harness.core.types.session.runtime import (
    CreateAgentSessionResult,
    CreateAgentSessionRuntimeOptions,
    CreateAgentSessionRuntimeResult,
)
from nova_harness.core.types.ui import UIContext
from nova_harness.core.ui.noop import NoOpUIContext
from nova_harness.core.utils.session_cwd import assert_session_cwd_exists
from nova_harness.core.utils.timings import print_timings, reset_timings, time

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_agent_session_services(
    options: Optional[CreateAgentSessionOptions] = None,
) -> AgentSessionServices:
    """创建 cwd 绑定的基础设施集合。"""
    options = options or CreateAgentSessionOptions()
    agent_dir = _resolve_agent_dir(options)
    cwd = _resolve_cwd(options)

    return await AgentSessionServices.create(
        cwd=cwd,
        agent_dir=str(agent_dir),
        auth_storage=options.auth_storage,
        settings_manager=options.settings_manager,
        model_registry=options.model_registry,
        resource_loader=options.resource_loader,
        extension_flag_values=options.extension_flag_values,
        project_trusted=options.project_trusted,
        resolve_project_trust=options.resolve_project_trust,
    )


async def create_agent_session_from_services(
    services: AgentSessionServices,
    session_manager: SessionManager,
    options: Optional[CreateAgentSessionOptions] = None,
) -> CreateAgentSessionResult:
    """从已创建的服务集合创建 AgentSession。"""
    options = options or CreateAgentSessionOptions()

    session_context = session_manager.build_session_context()
    is_continuing = len(session_context.messages) > 0

    initial_model_result = await find_initial_model(
        services=services,
        preferred_model=options.model,
        cli_provider=options.cli_provider,
        cli_model=options.cli_model,
        scoped_models=options.scoped_models,
        is_continuing=is_continuing,
        default_provider=services.settings_manager.get_default_provider(),
        default_model_id=services.settings_manager.get_default_model(),
        default_thinking_level=services.settings_manager.get_default_thinking_level(),
    )
    model = initial_model_result.model
    model_fallback_message = initial_model_result.fallback_message

    # 继续/恢复会话时优先恢复会话中保存的模型
    if is_continuing and session_context.model:
        saved_provider, saved_model_id = session_context.model
        restored = await restore_model_from_session(
            saved_provider=saved_provider,
            saved_model_id=saved_model_id,
            current_model=model,
            model_registry=services.model_registry,
        )
        if restored.model is not None:
            model = restored.model
        if restored.fallback_message:
            model_fallback_message = restored.fallback_message

    thinking_level = resolve_thinking_level(
        services=services,
        session_manager=session_manager,
        model=model,
        preferred_level=options.thinking_level or initial_model_result.thinking_level,
    )

    initial_active_tool_names = resolve_initial_active_tool_names(options)

    extension_runner_ref: dict[str, Optional[Any]] = {"current": None}

    agent = create_agent(
        services=services,
        session_manager=session_manager,
        model=model,
        thinking_level=thinking_level,
        extension_runner_ref=extension_runner_ref,
    )

    restore_or_persist_session_state(
        session_manager=session_manager,
        agent=agent,
        model=model,
        thinking_level=thinking_level,
    )

    config = build_agent_session_config(
        services=services,
        session_manager=session_manager,
        agent=agent,
        options=options,
        initial_active_tool_names=initial_active_tool_names,
        extension_runner_ref=extension_runner_ref,
    )

    session = AgentSession(config)
    extensions_result = services.resource_loader.get_extensions()

    return CreateAgentSessionResult(
        session=session,
        extensions_result=extensions_result,
        model_fallback_message=model_fallback_message,
    )


async def create_agent_session(
    options: Optional[CreateAgentSessionOptions] = None,
) -> CreateAgentSessionResult:
    """创建 AgentSession。"""
    options = options or CreateAgentSessionOptions()
    services = await create_agent_session_services(options)
    session_manager = resolve_session_manager(options, services)
    return await create_agent_session_from_services(services, session_manager, options)


async def create_agent_session_runtime(
    options: Optional[CreateAgentSessionOptions] = None,
) -> AgentSessionRuntime:
    """创建 AgentSession 并包装为 AgentSessionRuntime（保留给 CLI/RPC 使用）。"""
    options = options or CreateAgentSessionOptions()
    reset_timings()
    time("options resolved")
    services = await create_agent_session_services(options)
    time("services created")
    session_manager = resolve_session_manager(options, services)
    time("session manager resolved")
    result = await create_agent_session_from_services(
        services, session_manager, options
    )
    time("agent session created")
    session = result.session

    assert_session_cwd_exists(session_manager, services.cwd)

    resolved_ui_context = options.ui_context or NoOpUIContext()
    configure_extension_runner(session, resolved_ui_context)

    async def _create_runtime(
        runtime_options: CreateAgentSessionRuntimeOptions,
    ) -> CreateAgentSessionRuntimeResult:
        runtime_services = await create_agent_session_services(
            CreateAgentSessionOptions(
                cwd=runtime_options.cwd,
                agent_dir=runtime_options.agent_dir,
                auth_storage=services.auth_storage,
                settings_manager=services.settings_manager,
                model_registry=services.model_registry,
                resource_loader=services.resource_loader,
                agent_name=options.agent_name,
                extension_flag_values=options.extension_flag_values,
            )
        )
        runtime_result = await create_agent_session_from_services(
            runtime_services, runtime_options.session_manager, options
        )
        configure_extension_runner(runtime_result.session, resolved_ui_context)
        return CreateAgentSessionRuntimeResult(
            session=runtime_result.session,
            services=runtime_services,
            extensions_result=runtime_result.extensions_result,
            diagnostics=runtime_services.diagnostics,
            model_fallback_message=runtime_result.model_fallback_message,
        )

    runtime = AgentSessionRuntime(
        session=session,
        services=services,
        create_runtime=_create_runtime,
        diagnostics=services.diagnostics,
        model_fallback_message=result.model_fallback_message,
    )
    await session.bind_extensions({"ui_context": resolved_ui_context})
    time("extensions bound")
    print_timings()
    return runtime


def list_installed_agents() -> List[str]:
    """Return the names of all installed agent configs.

    Agents are discovered from installed packages (``<pkg>/agents/<name>/``).
    """
    names: set[str] = set()

    pm = PackageManager()
    for pkg in pm.list():
        pkg_agents_dir = Path(pkg.install_path) / AGENTS_DIR_NAME
        if not pkg_agents_dir.is_dir():
            continue
        for entry in pkg_agents_dir.iterdir():
            if entry.is_dir() and is_agent_dir(str(entry)):
                names.add(entry.name)

    return sorted(names)


async def create_agent_session_by_name(
    name: str,
    options: Optional[CreateAgentSessionOptions] = None,
) -> AgentSessionRuntime:
    """Launch an AgentSession using a pre-installed agent config by name."""
    options = options or CreateAgentSessionOptions()
    installed = list_installed_agents()
    if name not in installed:
        hint = (
            f" Installed agents: {', '.join(installed)}"
            if installed
            else " No agents installed yet."
        )
        raise FileNotFoundError(
            f"Agent '{name}' not found.\n"
            f"Run: nova pkg install /path/to/{name}\n"
            f"{hint}"
        )

    opts = CreateAgentSessionOptions(
        cwd=options.cwd,
        model=options.model,
        thinking_level=options.thinking_level,
        agent_name=name,
    )
    return await create_agent_session_runtime(opts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_agent_dir(options: CreateAgentSessionOptions) -> Path:
    agent_dir = Path(options.agent_dir) if options.agent_dir else get_agent_dir()
    agent_dir.mkdir(parents=True, exist_ok=True)
    return agent_dir


def _resolve_cwd(options: CreateAgentSessionOptions) -> str:
    if options.cwd:
        return str(options.cwd)
    if options.session_manager:
        return options.session_manager.get_cwd()
    return os.getcwd()


__all__ = [
    "create_agent_session",
    "create_agent_session_runtime",
    "create_agent_session_services",
    "create_agent_session_from_services",
    "create_agent_session_by_name",
    "list_installed_agents",
]
