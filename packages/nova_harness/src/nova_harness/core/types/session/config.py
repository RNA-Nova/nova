"""AgentSession 配置与创建选项类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from nova_agent import Agent, AgentTool
from nova_ai import Model, ThinkingLevel

from nova_harness.core.types.agent.model import ScopedModelConfig
from nova_harness.core.types.protocols import (
    AuthStorageProtocol,
    ExtensionRunnerProtocol,
    ModelRegistryProtocol,
    ResolveProjectTrustCallback,
    ResourceLoaderProtocol,
    SessionManagerProtocol,
    SettingsManagerProtocol,
    SystemPromptManagerProtocol,
    ToolsManagerProtocol,
)
from nova_harness.core.types.runtime.tools import ToolDefinition
from nova_harness.core.types.session_start_event import SessionStartEvent
from nova_harness.core.types.ui import UIContext


@dataclass
class AgentSessionConfig:
    """
    AgentSession 的完整配置。

    config 是扁平的字段集合，AgentSession 直接持有这些依赖。

    扩展系统由 AgentSession 在初始化时自行构建：它从 ``resource_loader``
    读取已加载的扩展，创建 ``ExtensionRunner``，并通过可选的
    ``extension_runner_ref`` 把 runner 引用暴露给外部（如 SDK）。

    ``system_prompt_manager`` 与 ``tools_manager`` 可由外部传入；
    否则 AgentSession 在 ``_build_runtime()`` 中自行创建。
    """

    agent: Agent
    session_manager: SessionManagerProtocol
    settings_manager: SettingsManagerProtocol
    cwd: str
    resource_loader: ResourceLoaderProtocol
    model_registry: ModelRegistryProtocol
    scoped_models: List[ScopedModelConfig] = field(default_factory=list)
    initial_active_tool_names: List[str] = field(
        default_factory=lambda: ["read", "bash", "edit", "write"]
    )
    base_tools_override: Optional[Dict[str, AgentTool]] = None
    allowed_tool_names: Optional[List[str]] = None
    excluded_tool_names: Optional[List[str]] = None
    no_tools: Optional[Literal["all", "builtin"]] = None
    custom_tools: Optional[List[ToolDefinition]] = None
    # 可选：AgentSession 内部创建 runner 后写回此 ref，供外部获取当前 runner
    extension_runner_ref: Optional[Dict[str, Optional[ExtensionRunnerProtocol]]] = (
        field(default=None, repr=False)
    )
    # 会话启动事件；由 AgentSession 在 runner 就绪后 emit
    session_start_event: Optional[SessionStartEvent] = field(default=None, repr=False)
    # 外部可传入已构造的 ToolsManager / SystemPromptManager；否则由 AgentSession 自行创建
    tools_manager: Optional[ToolsManagerProtocol] = field(default=None, repr=False)
    system_prompt_manager: Optional[SystemPromptManagerProtocol] = field(
        default=None, repr=False
    )

    def __post_init__(self):
        if not self.cwd:
            raise ValueError("cwd (current working directory) cannot be empty")
        if self.agent is None:
            raise ValueError("agent cannot be None")


@dataclass
class CreateAgentSessionOptions:
    """创建 AgentSession 的选项。"""

    cwd: Optional[Path] = None
    agent_dir: Optional[Path] = None
    auth_storage: Optional[AuthStorageProtocol] = None
    model_registry: Optional[ModelRegistryProtocol] = None
    model: Optional[Model] = None
    cli_provider: Optional[str] = None
    cli_model: Optional[str] = None
    thinking_level: Optional[ThinkingLevel] = None
    base_tools_override: Optional[Dict[str, AgentTool]] = None
    resource_loader: Optional[ResourceLoaderProtocol] = None
    session_manager: Optional[SessionManagerProtocol] = None
    settings_manager: Optional[SettingsManagerProtocol] = None
    agent_name: Optional[str] = None
    ui_context: Optional[UIContext] = None
    extension_flag_values: Optional[Dict[str, Any]] = None
    tools: Optional[List[str]] = None
    exclude_tools: Optional[List[str]] = None
    no_tools: Optional[Literal["all", "builtin"]] = None
    custom_tools: Optional[List[ToolDefinition]] = None
    scoped_models: Optional[List[ScopedModelConfig]] = None
    # None 表示不覆盖信任状态；由 resolve_project_trust 回调或默认策略决定
    project_trusted: Optional[bool] = None
    resolve_project_trust: Optional[ResolveProjectTrustCallback] = None


__all__ = [
    "AgentSessionConfig",
    "CreateAgentSessionOptions",
]
