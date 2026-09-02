"""AgentSession 配置与创建选项类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional

from nova_agent import Agent, AgentTool
from nova_ai import Model, ModelThinkingLevel
from nova_harness.core.types.protocols import (
    AuthStorageProtocol,
    ExtensionRunnerProtocol,
    ModelRuntimeProtocol,
    ResolveProjectTrustCallback,
    ResourceLoaderProtocol,
    SessionManagerProtocol,
    SettingsManagerProtocol,
    SystemPromptManagerProtocol,
    ToolsManagerProtocol,
)
from nova_harness.core.types.resources.tools import ToolDefinition
from nova_harness.core.types.session.model import ScopedModelConfig
from nova_harness.core.types.ui import UIContext

if TYPE_CHECKING:
    # 仅注解引用（dataclass 字段，运行时不求值）；types.events 的包级
    # re-export 链会经 types.compaction 回到 types.session，形成循环
    # import，因此只在 TYPE_CHECKING 下导入。
    from nova_harness.core.types.events.session import SessionStartEvent


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
    model_runtime: ModelRuntimeProtocol
    # 显式指定的 agent 配置名（对应 CreateAgentSessionOptions.agent_name）；
    # None 时回退到 resource_loader 的第一个可用 agent，再退到 "base_agent"。
    agent_name: Optional[str] = None
    # user 级 agents 目录的基准（AgentManager 影子写回 ``<agent_dir>/agents/``）；
    # 空串时回退全局默认 agent_dir
    agent_dir: str = ""
    scoped_models: List[ScopedModelConfig] = field(default_factory=list)
    # 三态：None=未指定（由 ToolsManager 默认激活注册表全部）；
    # []=显式不激活；[names]=显式激活集合
    initial_active_tool_names: Optional[List[str]] = None
    base_tools_override: Optional[Dict[str, AgentTool]] = None
    allowed_tool_names: Optional[List[str]] = None
    excluded_tool_names: Optional[List[str]] = None
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
    model_runtime: Optional[ModelRuntimeProtocol] = None
    model: Optional[Model] = None
    cli_provider: Optional[str] = None
    cli_model: Optional[str] = None
    thinking_level: Optional[ModelThinkingLevel] = None
    base_tools_override: Optional[Dict[str, AgentTool]] = None
    resource_loader: Optional[ResourceLoaderProtocol] = None
    session_manager: Optional[SessionManagerProtocol] = None
    settings_manager: Optional[SettingsManagerProtocol] = None
    agent_name: Optional[str] = None
    ui_context: Optional[UIContext] = None
    extension_flag_values: Optional[Dict[str, Any]] = None
    tools: Optional[List[str]] = None
    exclude_tools: Optional[List[str]] = None
    custom_tools: Optional[List[ToolDefinition]] = None
    scoped_models: Optional[List[ScopedModelConfig]] = None
    # 显式传入的纯静态资源（temporary scope，进程级生命周期，不进 settings、
    # 不经安装）——--skill / --prompt-template CLI 与 SDK 注入共用此通道
    # （对齐 pi 的单通道设计）；最低优先层，在 resolver 资源之后加载。
    additional_skill_paths: Optional[List[str]] = None
    additional_prompt_template_paths: Optional[List[str]] = None
    # 会话启动自愈重装包的进度回调（未提供时，若给了 ui_context 则自动
    # 桥接为 "package_progress" UI 通知）
    on_progress: Optional[Callable[[Any], None]] = None
    # None 表示不覆盖信任状态；由 resolve_project_trust 回调或默认策略决定
    project_trusted: Optional[bool] = None
    resolve_project_trust: Optional[ResolveProjectTrustCallback] = None


__all__ = [
    "AgentSessionConfig",
    "CreateAgentSessionOptions",
]
