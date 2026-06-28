"""
Nova Harness SDK — 创建和管理 AgentSession 的高层工厂函数。
"""

import os
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional

from nova_agent import Agent, AgentTool, ThinkingLevel
from nova_ai import Model
from nova_ai.types.base_model import NovaBaseModel

from nova_harness.core.agent_session import (
    AgentSession,
    AgentSessionConfig,
    AgentSessionRuntime,
    AgentSessionServices,
)
from nova_harness.core.agent_session.extensions import ExtensionRunner
from nova_harness.core.agent_session.services import CreateAgentSessionRuntimeResult
from nova_harness.core.config import AuthStorage, ModelRegistry, SettingsManager
from nova_harness.core.config.defaults import get_agent_dir, get_agents_dir
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.harness.system_prompt import SystemPromptManager
from nova_harness.core.resources.loader import ResourceLoader
from nova_harness.core.types.events import SessionStartEvent
from nova_harness.core.utils import resolve_api_key
from nova_harness.core.utils.messages import convert_to_llm


class CreateAgentSessionOptions(NovaBaseModel):
    """创建 AgentSession 的选项。"""

    model_config = NovaBaseModel.model_config.copy()
    model_config["arbitrary_types_allowed"] = True

    cwd: Optional[Path] = None
    agent_dir: Optional[Path] = None
    auth_storage: Optional[AuthStorage] = None
    model_registry: Optional[ModelRegistry] = None
    model: Optional[Model] = None
    thinking_level: Optional[ThinkingLevel] = None
    tools: Optional[Dict[str, AgentTool]] = None
    resource_loader: Optional[ResourceLoader] = None
    system_prompt_manager: Optional[SystemPromptManager] = None
    session_manager: Optional[SessionManager] = None
    settings_manager: Optional[SettingsManager] = None
    agent_name: Optional[str] = None


async def _resolve_initial_model(
    services: AgentSessionServices,
    preferred_model: Optional[Model] = None,
) -> Optional[Model]:
    """
    确定初始模型。

    优先级：
    1. 调用方显式指定的 model
    2. 当前会话上下文中保存的模型（且已配置鉴权）
    3. settings 中的默认模型
    4. 内置 fallback：volcengine/deepseek-v3-2-251201
    """
    if preferred_model is not None:
        return preferred_model

    registry = services.model_registry
    session_context = services.session_manager.build_session_context()

    if session_context.model:
        provider, model_id = session_context.model
        restored = registry.find(provider, model_id)
        if restored and await registry.get_api_key(restored):
            return restored

    default_provider = services.settings_manager.get_default_provider()
    default_model_id = services.settings_manager.get_default_model()
    if default_provider and default_model_id:
        model = registry.find(default_provider, default_model_id)
        if model and await registry.get_api_key(model):
            return model

    fallback = registry.find("volcengine", "deepseek-v3-2-251201")
    if fallback and await registry.get_api_key(fallback):
        return fallback

    # 最后返回任意一个有鉴权的可用模型
    for model in registry.get_available():
        if await registry.get_api_key(model):
            return model
    return None


def _resolve_thinking_level(
    services: AgentSessionServices,
    model: Optional[Model],
    preferred_level: Optional[ThinkingLevel] = None,
) -> Optional[ThinkingLevel]:
    """确定初始思考级别，优先恢复会话上下文中的级别。

    当上下文中有 thinking_level_change 且显式设为 None 时，表示 off，应被保留。
    """
    if preferred_level is not None:
        level = preferred_level
    else:
        session_context = services.session_manager.build_session_context()
        has_thinking_entry = any(
            e.type == "thinking_level_change"
            for e in services.session_manager.get_branch()
        )
        default_level = (
            services.settings_manager.get_default_thinking_level()
            or ThinkingLevel.MEDIUM
        )
        if has_thinking_entry:
            # None 表示显式关闭（off）
            level = session_context.thinking_level
        else:
            level = default_level

    if level is None:
        return None

    if model is None:
        return ThinkingLevel.MINIMAL

    supported = getattr(model, "thinking_level_map", None) or {}
    available = [ThinkingLevel.MINIMAL]
    for key in ["low", "medium", "high", "xhigh"]:
        if key in supported:
            available.append(ThinkingLevel(key))
    if level.value in [a.value for a in available]:
        return level
    # 降级到最高可用级别
    return available[-1]


async def _create_session_for_services(
    services: AgentSessionServices,
    preferred_model: Optional[Model] = None,
    preferred_thinking_level: Optional[ThinkingLevel] = None,
    base_tools_override: Optional[Dict[str, AgentTool]] = None,
    session_start_event: Optional[SessionStartEvent] = None,
) -> AgentSession:
    """根据服务集合创建新的 AgentSession（将 services 解包成扁平 config）。"""
    model = await _resolve_initial_model(services, preferred_model)
    thinking_level = _resolve_thinking_level(services, model, preferred_thinking_level)

    get_api_key = partial(resolve_api_key, model_registry=services.model_registry)

    extension_runner_ref: Dict[str, Optional[ExtensionRunner]] = {"current": None}

    agent_kwargs = {
        "initial_state": {
            "system_prompt": None,
            "model": model,
            "thinking_level": thinking_level,
            "tools": [],
        },
        "convert_to_llm": convert_to_llm,
        "steering_mode": services.settings_manager.get_steering_mode(),
        "follow_up_mode": services.settings_manager.get_follow_up_mode(),
        "session_id": services.session_manager.get_session_id(),
        "get_api_key": get_api_key,
        "thinking_budgets": services.settings_manager.get_thinking_budgets(),
        "max_retry_delay_ms": services.settings_manager.get_retry_settings().max_delay_ms,
    }

    agent = Agent(**agent_kwargs)

    initial_active_tool_names = (
        services.system_prompt_manager.get_default_active_tool_names()
    )

    config = AgentSessionConfig(
        agent=agent,
        session_manager=services.session_manager,
        settings_manager=services.settings_manager,
        cwd=services.cwd,
        system_prompt_manager=services.system_prompt_manager,
        resource_loader=services.resource_loader,
        model_registry=services.model_registry,
        scoped_models=[],
        initial_active_tool_names=initial_active_tool_names,
        base_tools_override=base_tools_override,
        extension_runner_ref=extension_runner_ref,
        services=services,
        session_start_event=session_start_event,
    )
    return AgentSession(config)


async def create_agent_session(options: CreateAgentSessionOptions = None):
    """创建 AgentSession 并包装为 AgentSessionRuntime 返回。"""
    options = CreateAgentSessionOptions() if not options else options
    agent_dir = Path(options.agent_dir) if options.agent_dir else get_agent_dir()
    agent_dir.mkdir(parents=True, exist_ok=True)

    cwd = str(options.cwd) if options.cwd else os.getcwd()

    cleaned_cwd = cwd.lstrip("/\\").replace("/", "-").replace("\\", "-")
    safe_path = f"--{cleaned_cwd}--"
    session_dir = os.path.join(agent_dir, "sessions", safe_path)
    os.makedirs(session_dir, exist_ok=True)

    # 创建 cwd 绑定的服务集合（不含 session_manager）
    services = await AgentSessionServices.create(
        cwd=cwd,
        agent_dir=str(agent_dir),
        auth_storage=options.auth_storage,
        settings_manager=options.settings_manager,
        model_registry=options.model_registry,
        resource_loader=options.resource_loader,
        system_prompt_manager=options.system_prompt_manager,
        agent_name=options.agent_name,
    )

    # 初始 session manager 可由调用方传入，否则按规则创建
    session_manager = options.session_manager or SessionManager.create(cwd, session_dir)
    services.session_manager = session_manager

    base_tools_override = options.tools if options.tools else None

    async def _create_runtime(
        runtime_cwd: str,
        runtime_agent_dir: str,
        runtime_session_manager: SessionManager,
        session_start_event: Optional[SessionStartEvent] = None,
    ) -> CreateAgentSessionRuntimeResult:
        """Runtime 工厂：为新的会话重新创建 services + session。"""
        runtime_services = await AgentSessionServices.create(
            cwd=runtime_cwd,
            agent_dir=runtime_agent_dir,
            auth_storage=services.auth_storage,
            settings_manager=services.settings_manager,
            model_registry=services.model_registry,
            resource_loader=services.resource_loader,
            system_prompt_manager=services.system_prompt_manager,
        )
        runtime_services.session_manager = runtime_session_manager

        session = await _create_session_for_services(
            runtime_services,
            preferred_model=None,
            preferred_thinking_level=None,
            base_tools_override=base_tools_override,
            session_start_event=session_start_event,
        )

        return CreateAgentSessionRuntimeResult(
            session=session,
            services=runtime_services,
            diagnostics=runtime_services.diagnostics,
            model_fallback_message=None,
        )

    session = await _create_session_for_services(
        services,
        preferred_model=options.model,
        preferred_thinking_level=options.thinking_level,
        base_tools_override=base_tools_override,
        session_start_event=SessionStartEvent(reason="new"),
    )

    runtime = AgentSessionRuntime(
        session,
        services,
        _create_runtime,
        diagnostics=services.diagnostics,
        model_fallback_message=None,
    )
    session.bind_runtime(runtime)
    await session.bind_extensions()

    return runtime


def list_installed_agents() -> List[str]:
    """Return the names of all installed agent configs.

    Scans ``~/.nova/agent/agents/`` for valid agent directories.
    """
    agents_dir = str(get_agents_dir())
    if not os.path.exists(agents_dir):
        return []
    return sorted(
        name
        for name in os.listdir(agents_dir)
        if os.path.isdir(os.path.join(agents_dir, name))
        and os.path.exists(os.path.join(agents_dir, name, "description.md"))
    )


async def create_agent_session_by_name(
    name: str,
    options: Optional[CreateAgentSessionOptions] = None,
) -> "AgentSessionRuntime":
    """Launch an AgentSession using a pre-installed agent config by name.

    The agent config must exist under ``~/.nova/agent/agents/<name>/``.
    Tools are automatically discovered from ``~/.nova/agent/tools/`` (and the
    project-level ``./.nova/tools/``) by ``ResourceLoader`` and filtered by the
    agent's ``tools.json`` whitelist through ``SystemPromptManager``.

    Args:
        name: Agent config directory name (e.g. ``"coding_agent"``).
        options: Optional session overrides (cwd, model, thinking_level, etc.).

    Raises:
        FileNotFoundError: If the agent config is not installed.
    """
    agent_dir = os.path.join(get_agents_dir(), name)
    if not os.path.exists(agent_dir):
        agents = list_installed_agents()
        hint = (
            f" Installed agents: {', '.join(agents)}"
            if agents
            else " No agents installed yet."
        )
        raise FileNotFoundError(
            f"Agent '{name}' not found at {agent_dir}\n"
            f"Run: nova pkg install /path/to/{name}\n"
            f"{hint}"
        )

    opts = CreateAgentSessionOptions(
        cwd=options.cwd if options else None,
        model=options.model if options else None,
        thinking_level=options.thinking_level if options else None,
        agent_name=name,
    )
    return await create_agent_session(opts)
