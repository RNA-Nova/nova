from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import List, Optional

from nova_ai import Model
from .agent import AgentSession,AgentSessionConfig 

from .session import SessionManager
from .setting import SettingsManager
from .computex import ComputexManager
from .model_registry import ModelRegistry, AuthStorage
from .resource import DefaultResourceLoader, DefaultResourceLoaderOptions, ResourceLoader
from .definition import AgentDefinitor
from pi_agent import Agent, AgentTool,ThinkingLevel
from .messages import convert_to_llm
from .config import get_agent_dir, CONFIG_DIR_NAME
import os

from .utils import resolve_api_key

@dataclass
class CreateAgentSessionOptions:
    """
    Options for creating an agent session.
    
    Attributes:
        cwd: Working directory for project-local discovery. Default: current working directory
        agent_dir: Global config directory. Default: ~/.pi/agent
        auth_storage: Auth storage for credentials. Default: AuthStorage.create(agent_dir/auth.json)
        model_registry: Model registry. Default: new ModelRegistry(auth_storage, agent_dir/models.json)
        model: Model to use. Default: from settings, else first available
        thinking_level: Thinking level. Default: from settings, else 'medium' (clamped to model capabilities)
        scoped_models: Models available for cycling (Ctrl+P in interactive mode)
        tools: Built-in tools to use. Default: codingTools [read, bash, edit, write]
        custom_tools: Custom tools to register (in addition to built-in tools)
        resource_loader: Resource loader. When omitted, DefaultResourceLoader is used
        session_manager: Session manager. Default: SessionManager.create(cwd)
        settings_manager: Settings manager. Default: SettingsManager.create(cwd, agent_dir)
    """
    cwd: Optional[Path] = None
    agent_dir: Optional[Path] = None
    auth_storage: Optional[AuthStorage] = None
    model_registry: Optional[ModelRegistry] = None
    model: Optional[Model] = None
    thinking_level: Optional[ThinkingLevel] = None
    tools: Optional[List[AgentTool]] = None
    resource_loader: Optional[ResourceLoader] = None
    agent_definitior: Optional[AgentDefinitor] = None
    session_manager: Optional[SessionManager] = None
    settings_manager: Optional[SettingsManager] = None
    computex_manager: Optional[ComputexManager] = None

async def create_agent_session(options: CreateAgentSessionOptions = None):
    options = CreateAgentSessionOptions() if not options else options
    agent_dir = options.agent_dir or get_agent_dir()
    cwd = options.cwd or os.getcwd()
    session_manager = SessionManager.create(cwd) if not options.session_manager else options.session_manager
    settings_manager = SettingsManager.create(cwd,agent_dir) if not options.settings_manager else options.settings_manager
    computex_manager = ComputexManager() if not options.computex_manager else options.computex_manager
    auth_path = agent_dir / 'auth.json'
    models_json_path = agent_dir / 'models.json'
    auth_storage = AuthStorage.create(auth_path) if not options.auth_storage else options.auth_storage
    model_registry = ModelRegistry(auth_storage,models_json_path) if not options.model_registry else options.model_registry
    resource_loader_options = DefaultResourceLoaderOptions(
        cwd=cwd,
        agent_dir=agent_dir,
        additional_prompt_template_paths=[],
        no_prompt_templates=False,
    )
    resource_loader = DefaultResourceLoader(resource_loader_options) if not options.resource_loader else options.resource_loader
    agent_definition_dir = Path(cwd) / CONFIG_DIR_NAME / "definition"
    agent_definitor = AgentDefinitor(agent_definition_dir) if not options.agent_definitior else options.agent_definitior
    get_api_key = partial(
        resolve_api_key, 
        model_registry=model_registry
    )
    agent = Agent(
        initial_state = {
            'system_prompt':None,
            'model':model_registry.find("volcengine", "deepseek-r1-250528"),
            'thinking_level':ThinkingLevel.MEDIUM,
            'tools':[],
        },
        convert_to_llm=convert_to_llm,
        steering_mode = settings_manager.get_steering_mode(),
        follow_up_mode = settings_manager.get_follow_up_mode(),
        session_id = session_manager.get_session_id(),
        get_api_key = get_api_key,
        thinking_budgets = settings_manager.get_thinking_budgets(),
        max_retry_delay_ms = settings_manager.get_retry_settings().max_delay_ms
    )
    system_prompt_fn = partial(
        agent_definitor.build_system_prompt,
        include_user = True,
        include_tools = True,
        include_dynamic = False,
    )
    config = AgentSessionConfig(
        agent=agent,
        system_prompt_fn=system_prompt_fn,
        session_manager=session_manager,
        settings_manager=settings_manager,
        computex_manager=computex_manager,
        cwd=cwd,
        resource_loader=resource_loader,
        model_registry=model_registry,
        initial_active_tool_names=[
            'execute_command',
            'skill_tool',
            'write',
            'read',
            'send_to_frontend',
        ],
        base_tools_override=options.tools if options.tools else None
    ) 
    agent_session = AgentSession(config)
    if options.model:
        agent_session.set_model(options.model)
    if options.thinking_level:
        agent_session.set_thinking_level(options.thinking_level)
    return agent_session